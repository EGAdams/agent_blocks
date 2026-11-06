#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Bridge Worker -- take one ChatGPT task from the agent-bridge gateway and run it here.

Outbound only: polls the gateway over HTTPS, claims one task with a lease,
checks it against this machine's own policy, hands it to the on-machine agent
through `agent_comms`' post office, and publishes the answer back. Read
operations (inspect, explain, search_memory) run as before with
`codex exec --sandbox read-only`. The one write operation, `implement`, runs
only in `workspace_write` mode, only for an agent whose card declares it as a
write, and only in a workspace that `agent_bridge_worker/workspaces.json`
marks `writes: true` and that passes its checks (a git repository directly
under the write root, no symlinks, nothing the bridge itself runs from); the
Codex card runs it with `--sandbox workspace-write` jailed to that one
directory. `scan` runs only in `device_control` mode, for the `scanner` agent,
and only when `~/.config/scan_bridge/worker_token` exists: the ScannerAgent
starts one Window or Freezer scan through the dashboard's loopback scan
façade (keyed by the task id, so a retry never scans twice), follows that scan
by its id, uploads its pages and text to the gateway and waits for Mazda's
verdict -- no model and no shell in the path. `print` and `print_cancel` also
run only in `device_control` mode, and `print_status` / `print_discover` in
`read_only`, all for the `printer` agent and only when
`~/.config/startrek_printer/enabled` exists: the PrinterAgent runs
`adw_printer.py` once per task (keyed by the task id, so a retry never prints
twice) and never stops a print it started. No inbound
port, no shell built from the request. Run it once (default), `--drain` until
the queue is empty, or `--watch` to keep checking (what the
`agent-bridge-worker` user service runs). In watch mode an idle check is one
GET and nothing else; how often it checks is `agent_bridge_worker/
poll_settings.json`, read through `agent_bridge_worker.polling`.

    Phases:  ClaimTask -> ResolveAgent -> ResolveWorkspace -> PrepareExecution
             -> PrepareWorkspace -> ExecuteAgent -> VerifyChanges -> CollectResult
             -> CollectEvents -> PublishResult -> FinalizeTask

PrepareWorkspace, VerifyChanges and CollectResult do nothing for a read task.

The business logic is `agent_bridge_worker.service.BridgeTaskRun`; each phase
here calls one of its methods, so this orchestration can be swapped for
`BridgeTaskRun.run_one()` without touching the logic.

    BRIDGE_URL=https://bridge.example/api/agent-bridge/v1 \\
    BRIDGE_WORKER_TOKEN_FILE=~/.config/agent_bridge/worker_token \\
    lancedb_memory/.venv/bin/python adws/adw_bridge_worker.py [--drain]
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

_HERE = Path( __file__ ).resolve().parent
for _path in ( _HERE, _HERE.parent / "lancedb_memory" ):
    if str( _path ) not in sys.path:
        sys.path.insert( 0, str( _path ))

from sssf_roles import ( ConsoleLifecycleLog, ILifecycleLog, IPhase, IPhaseHandle, LifecycleLogged,  # noqa: E402
                         ManualContextPhaseExecutor, PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams  # noqa: E402

from agent_bridge_worker.models import RunReport  # noqa: E402
from agent_bridge_worker.service import BridgeTaskRun  # noqa: E402

REQUIRED_AGENTS: list[ str ] = []          # no model in the worker; Codex runs behind the post office
SENDER     = "chatgpt-bridge"
WORKSPACES_FILE = _HERE.parent / "lancedb_memory" / "agent_bridge_worker" / "workspaces.json"
WRITE_DATA = _HERE / "adw_data" / "bridge_write"      # journal, patches, locks (gitignored with adw_data)
SCAN_FACADE_URL = os.environ.get( "SCAN_BRIDGE_URL", "http://127.0.0.1:8765/api/scan-bridge/v1" )
SCAN_TOKEN_FILE = Path.home() / ".config" / "scan_bridge" / "worker_token"
SCAN_WORKDIR    = _HERE / "adw_data" / "scan_bridge"    # the `scanners` workspace: empty, nothing runs in it
PRINT_ENABLED   = Path.home() / ".config" / "startrek_printer" / "enabled"   # printing is off until this exists
PRINT_WORKDIR   = _HERE / "adw_data" / "printer" / "workspace"   # the `printers` workspace: empty, nothing runs in it
PRINT_ADW       = _HERE / "adw_printer.py"
NOTES_ENABLED   = Path.home() / ".config" / "startrek_notes" / "enabled"     # the Notes diagnostic is off until this exists
NOTES_DIR       = Path( os.environ.get( "NOTES_DIR", str( Path.home() / "notes" )))   # the dashboard's NOTES_DIR
NOTES_URL       = os.environ.get( "NOTES_ENDPOINT_URL", "http://127.0.0.1:8765" )     # the dashboard, loopback
NOTES_WORKDIR   = _HERE / "adw_data" / "notes_bridge" / "workspace"   # the `notes` workspace: empty, nothing runs in it
NOTES_TOKEN_FILE = Path.home() / ".config" / "startrek_notes" / "notes_token"   # agent-bridge-notes: notes:read + notes:write
NOTES_ACTIVITY  = Path( os.environ.get( "NOTES_ACTIVITY_LOG", str( Path.home() / ".cache" / "agent_bridge" / "notes_activity.jsonl" )))
UV              = Path.home() / ".local" / "bin" / "uv"
MAX_DRAIN  = 20
POLL_SETTINGS = _HERE.parent / "lancedb_memory" / "agent_bridge_worker" / "poll_settings.json"
EXIT_NOT_AUTHORISED = 78   # EX_CONFIG; the systemd unit's RestartPreventExitStatus


class SystemClock:
    def monotonic( self ) -> float:
        return time.monotonic()

    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )


class SignalWaiter:
    """IWaiter: sleeps on an Event that SIGTERM / SIGINT set, so systemd stops it promptly."""

    def __init__( self ) -> None:
        self._stop = threading.Event()
        for sig in ( signal.SIGTERM, signal.SIGINT ):
            signal.signal( sig, lambda *_: self._stop.set())

    @property
    def stopping( self ) -> bool:
        return self._stop.is_set()

    def wait( self, seconds: float ) -> bool:
        return self._stop.wait( seconds )


class ServicePhase( LifecycleLogged, IPhase ):
    """One SSSF phase that calls one BridgeTaskRun method."""

    def __init__( self, name: str, description: str, act: Callable[[], Any ], logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._name, self._description, self._act = name, description, act

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="chatgpt-bridge", description=self._description )

    def act( self, handle: IPhaseHandle ) -> Any:
        return self._act()


def build_application( base_url: str, worker_id: str, hooks: Any = None ) -> BridgeTaskRun:
    """The composition root: the only place that names a concrete. `hooks` (an object_trace AdwRunTrace) traces
    the gateway and every agent adapter as objects; without it nothing is traced."""
    from agent_bridge_worker.adapters.git_changes import GitChangeTracker
    from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway, load_worker_token
    from agent_bridge_worker.adapters.jailed_checks import JailedCheckRunner
    from agent_bridge_worker.adapters.json_journal import JsonWriteJournal
    from agent_bridge_worker.adapters.post_office_agent import PostOfficeAgent
    from agent_bridge_worker.repeat_answers import JsonAnswerStore, RepeatAnswers
    from agent_bridge_worker.adapters.workspace_lock import FlockWorkspaceLock
    from agent_bridge_worker.adapters.http_scan_facade import HttpScanFacade, load_scan_token
    from agent_bridge_worker.ports import GatewayError
    from agent_bridge_worker.policy import FixedFirstWorkspaces, ScopedPolicy, scoped_operations
    from agent_bridge_worker.scanner_agent import ScannerAgent
    from agent_bridge_worker.printer_agent import PrinterAgent
    from agent_bridge_worker.notes_agent import NotesAgent
    from agent_bridge_worker.adapters.notes_store import FilesystemNotesStore, HttpNotesEndpoint
    from agent_bridge_worker.adapters.notes_bridge_http import HttpNotesBridge
    from agent_bridge_worker.adapters.notes_activity_log import JsonlNotesActivity
    from agent_bridge_worker.adapters.printer_process import UvPrinterCommands
    from agent_bridge_worker.redaction import SecretScanner
    from agent_bridge_worker.workspaces import ConfiguredWorkspaces
    from agent_bridge_worker.write_session import ScopedWriteSession
    from agent_comms.executors.jail import BwrapJail, WorkspacePrivatePaths
    from agent_comms.factory import build_post_office
    from agent_comms.models import TaskRequest

    clock      = SystemClock()
    token      = load_worker_token()
    kit        = build_post_office( forward_failures_to_mechanic=False )
    gateway    = HttpTaskGateway( base_url, token )
    if hooks is not None:                               # observe only: every call and result passes through unchanged
        from agent_bridge_worker.adapters.traced import TracingGateway
        from object_trace.factory import default_tracer
        gateway = TracingGateway( gateway, default_tracer(), lambda: hooks.current )
        hooks.closing( gateway.close )

    def relay( task, line: str ) -> None:              # a worker's real steps, shown live at the gateway
        try:
            gateway.event( task, "progress", line )
        except GatewayError:
            pass
    offered    = scoped_operations( kit.registry, SENDER )
    agents     = {}
    for name in offered:
        card    = kit.registry.card( name )
        timeout = float( card.settings.get( "timeout_seconds", 600 )) + 60
        agents[ name ] = PostOfficeAgent( SENDER, kit.dispatcher, kit.replies, kit.canceller, kit.board,
                                          kit.event_log, TaskRequest, clock, timeout_seconds=timeout, relay=relay )
    events     = next( iter( agents.values()), None )
    if events is None:
        raise RuntimeError( f"no agent in the post office accepts work from { SENDER }" )
    answers    = JsonAnswerStore( Path.home() / ".cache" / "agent_bridge" / "repeat_answers.json" )
    agents     = { name: RepeatAnswers( agent, answers, time.time ) for name, agent in agents.items()}
    home       = Path.home()
    workspaces = ConfiguredWorkspaces.load( WORKSPACES_FILE, protected=[
        _HERE, _HERE.parent / "lancedb_memory" / "agent_bridge_worker", _HERE.parent / "lancedb_memory" / "agent_comms",
        _HERE.parent / "agent_bridge", home / ".config" / "agent_bridge", home / ".codex-bridge", home / ".codex",
        home / ".claude", home / ".claude-bridge" ])
    for name, problem in workspaces.problems().items():
        print( f"workspace { name } is not write-enabled: { problem }", flush=True )
    check_jail = BwrapJail( WorkspacePrivatePaths(), state_dirs=[], env={ "PYTHONDONTWRITEBYTECODE": "1" }, network=False )

    def settled( local_task_id: str ) -> bool:
        envelope = kit.board.task( local_task_id )
        status   = getattr( getattr( envelope, "status", None ), "value", "" )
        return envelope is None or status in ( "succeeded", "failed", "cancelled", "timed_out" )

    writes = ScopedWriteSession(
        FlockWorkspaceLock( WRITE_DATA / "locks" ), GitChangeTracker(),
        JailedCheckRunner( lambda argv, root: check_jail.wrap( argv, root, root, [])),
        JsonWriteJournal( WRITE_DATA ), workspaces.checks, settled, SecretScanner( known=[ token ]),
        write_problem=workspaces.write_problem )
    modes   = { "read_only", "workspace_write" }
    if SCAN_TOKEN_FILE.exists():                        # scanning is off until the façade token is provisioned
        SCAN_WORKDIR.mkdir( mode=0o700, parents=True, exist_ok=True )
        agents[ "scanner" ]  = ScannerAgent( HttpScanFacade( SCAN_FACADE_URL, load_scan_token( SCAN_TOKEN_FILE )),
                                             gateway, clock )
        offered[ "scanner" ] = { "scan": False }
        modes.add( "device_control" )
    if PRINT_ENABLED.exists():                          # printing is off until EG creates the flag file
        PRINT_WORKDIR.mkdir( mode=0o700, parents=True, exist_ok=True )
        agents[ "printer" ]  = PrinterAgent( UvPrinterCommands( UV, PRINT_ADW, _HERE.parent ), clock )
        offered[ "printer" ] = { "print": False, "print_cancel": False, "print_status": False, "print_discover": False }
        modes.add( "device_control" )
    write_names = workspaces.write_names()
    if NOTES_ENABLED.exists():                          # the Notes diagnostic is off until EG creates the flag file
        NOTES_WORKDIR.mkdir( mode=0o700, parents=True, exist_ok=True )
        # Approved-note list/read/update go through the dashboard's token interface; without the token
        # file they are refused as "not provisioned" and only the diagnostic operations run.
        bridge = ( HttpNotesBridge( NOTES_URL, load_scan_token( NOTES_TOKEN_FILE ))
                   if NOTES_TOKEN_FILE.exists() else None )
        agents[ "notes" ]  = NotesAgent( FilesystemNotesStore( NOTES_DIR ), HttpNotesEndpoint( NOTES_URL ), bridge,
                                         JsonlNotesActivity( NOTES_ACTIVITY ))
        offered[ "notes" ] = { "inspect": False, "implement": True }
        write_names = write_names | { "notes" }
    resolver = FixedFirstWorkspaces({ "scanners": SCAN_WORKDIR, "printers": PRINT_WORKDIR, "notes": NOTES_WORKDIR },
                                    workspaces )
    if hooks is not None:
        from agent_bridge_worker.adapters.traced import TracingAgent
        from object_trace.factory import default_tracer
        agents = { name: TracingAgent( agent, default_tracer(), name ) for name, agent in agents.items()}
    return BridgeTaskRun( gateway, ScopedPolicy( offered, write_names, frozenset( modes )),
                          resolver, agents, events, clock, worker_id, writes=writes, self_contained=frozenset({ "notes" }))


def build_watcher( base_url: str, settings_path: Path, run_work: Callable[[], int ]) -> Any:
    """The composition root for `--watch`: a cheap probe, the schedule, the waiter."""
    from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway, load_worker_token
    from agent_bridge_worker.adapters.poll_state_file import JsonPollStateFile
    from agent_bridge_worker.polling import build_schedule, load_settings
    from agent_bridge_worker.watch import PollLoop

    probe    = HttpTaskGateway( base_url, load_worker_token())
    settings = load_settings( settings_path )
    say      = lambda line: print( f"{ datetime.now():%Y-%m-%d %H:%M:%S} watch: { line }", flush=True )  # noqa: E731
    say( f"settings { settings.model_dump_json()}" )
    now      = lambda: datetime.now().astimezone()  # noqa: E731 -- offset-aware, so the dashboard can count down
    return PollLoop( lambda: probe.next() is not None, run_work, build_schedule( settings ),
                     SignalWaiter(), now, say, observers=[ JsonPollStateFile()])


def phases( svc: BridgeTaskRun ) -> list[ tuple[ str, str, Callable[[], Any ]]]:
    return [
        ( "claim_task",        "Take the next queued task's lease, if there is one", svc.claim_task ),
        ( "resolve_agent",     "Check this worker's policy (operation, mode, agent, workspace) and pick the adapter", svc.resolve_agent ),
        ( "resolve_workspace", "Turn the workspace name into a directory on this machine", svc.resolve_workspace ),
        ( "prepare_execution", "Last cancel / lease check before anything runs", svc.prepare_execution ),
        ( "prepare_workspace", "Write tasks only: lock the workspace and record a baseline", svc.prepare_workspace ),
        ( "execute_agent",     "Hand off through the post office and wait, heartbeating", svc.execute_agent ),
        ( "verify_changes",    "Write tasks only: what changed since the baseline, then the configured checks", svc.verify_changes ),
        ( "collect_result",    "Write tasks only: files, diff and checks as one report", svc.collect_result ),
        ( "collect_events",    "Forward the on-machine audit trail (names and times only)", svc.collect_events ),
        ( "publish_result",    "Tell the gateway how it ended", svc.publish_result ),
        ( "finalize_task",     "Report what this run did", svc.finalize_task ),
    ]


def one_run( args: argparse.Namespace, cfg: Any, boot: ILifecycleLog ) -> RunReport:
    from object_trace.adw_hooks import AdwRunTrace
    from object_trace.factory import default_tracer

    run      = SessionRunFactory( boot ).create( cfg, args.adw_id )
    log      = ConsoleLifecycleLog( run )
    executor = ManualContextPhaseExecutor( boot )
    svc: BridgeTaskRun | None = None
    task_of  = lambda: svc.task.task_id if svc is not None and svc.task is not None else None  # noqa: E731
    # The trace shares the SSSF run's adw_id, so the inspector joins the two. It only observes (OBJECT_TRACE=off stops it).
    with AdwRunTrace( default_tracer(), "adw_bridge_worker", run.adw_id, task_id_of=task_of ) as hooks:
        try:
            svc = default_tracer().construct( "bridge.worker_app", lambda: build_application( args.url, args.worker_id, hooks ),
                                              interface="BridgeTaskRun", label="the worker's composition root" )
            for name, description, act in phases( svc ):
                executor.execute( run, ServicePhase( name, description, hooks.phase( name, act, description ), log ))
        except BaseException as error:  # noqa: BLE001 -- the lease expires and the gateway retries; the run log has the traceback
            run.finish( accepted=False, reason=f"{ type( error ).__name__ }: { error }"[ :300 ])
            raise
        finally:
            if svc is not None:
                svc.close()            # a write task's workspace lock, whatever happened
    report = svc.report
    run.finish( accepted=True, reason=( report.summary or "done" )[ :300 ])
    return report


def describe( report: RunReport ) -> str:
    if report.task_id is None:
        return report.summary or "nothing queued"
    outcome = report.outcome.value if report.outcome else "unknown"
    return f"{ report.task_id } ({ report.agent }): { outcome }, gateway state { report.state or '-' } -- { report.summary }"


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--url", default=os.environ.get( "BRIDGE_URL", "" ), help="gateway base, ending /api/agent-bridge/v1" )
    parser.add_argument( "--worker-id", default=f"wsl-{ socket.gethostname().lower() }" )
    parser.add_argument( "--drain", action="store_true", help=f"keep going until nothing is queued (at most { MAX_DRAIN })" )
    parser.add_argument( "--watch", action="store_true", help="keep checking for work, paced by --poll-settings" )
    parser.add_argument( "--poll-settings", default=str( POLL_SETTINGS ))
    parser.add_argument( "--message-out", default="" )
    parser.add_argument( "--config", default=str( _HERE / "adw_sssf_config" / "sssf.config.yaml" ))
    parser.add_argument( "--adw-id", default=None )
    args = parser.parse_args( argv )
    if not args.url:
        parser.error( "set --url or BRIDGE_URL" )

    boot: ILifecycleLog = PrintLifecycleLog()
    loader = SssfConfigLoader( boot )
    cfg = loader.load( args.config )
    loader.validate( cfg, REQUIRED_AGENTS )

    if args.watch:
        from agent_bridge_worker.polling import WorkerNotAuthorised

        def drain_and_count() -> int:
            reports = drain( args, cfg, boot, MAX_DRAIN )
            for report in reports:
                print( describe( report ), flush=True )
            return sum( 1 for report in reports if report.task_id is not None )

        try:
            build_watcher( args.url, Path( args.poll_settings ), drain_and_count ).run()
        except WorkerNotAuthorised as error:
            print( f"stopping: { error } -- check the worker token", flush=True )
            return EXIT_NOT_AUTHORISED
        return 0

    reports = drain( args, cfg, boot, MAX_DRAIN if args.drain else 1 )
    text = "\n".join( describe( report ) for report in reports )
    if args.message_out:
        Path( args.message_out ).write_text( text + "\n" )
    print( text )
    return 0


def drain( args: argparse.Namespace, cfg: Any, boot: ILifecycleLog, limit: int ) -> list[ RunReport ]:
    """Run the ADW up to `limit` times, stopping at the first pass that found nothing."""
    reports: list[ RunReport ] = []
    for _ in range( limit ):
        report = one_run( args, cfg, boot )
        reports.append( report )
        if report.task_id is None:
            break
    return reports


if __name__ == "__main__":
    sys.exit( main())

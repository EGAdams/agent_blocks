"""WorkerSupervisor: notice a stopped or stalled bridge worker and restart it, a bounded number of times.

Runs from `agent-bridge-supervisor.timer` (every 60 s), outside the Bridge task
queue: it never talks to the gateway and never claims a task. systemd's own
`Restart=on-failure` only covers a crash; this also covers a worker that exited
cleanly, was left stopped, or is alive but no longer checking for work.

    Observe (systemd unit, /proc cmdline, poll_state.json, last journal line)
      -> assess()      pure: healthy | starting | stalled | stopped | unauthorised | held
      -> act           restart only for stalled/stopped, at most `max_restarts` per hour
      -> report        ~/.cache/agent_bridge/worker_health.json (the dashboard reads it) + journal

Never restarts after exit 78 (the gateway rejected the worker token: restarting
only repeats it). `touch ~/.config/agent_bridge/supervisor_hold` pauses it, for
a deliberate stop.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.supervisor            # one pass (what the timer runs)
    .venv/bin/python -m agent_bridge_worker.supervisor --dry-run  # assess and report, never restart
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .lanes import WorkerLane

UNIT            = "agent-bridge-worker.service"
WORKER_SCRIPT   = "adw_bridge_worker.py"
CACHE           = Path.home() / ".cache" / "agent_bridge"
POLL_STATE      = CACHE / "poll_state.json"
HEALTH_FILE     = CACHE / "worker_health.json"
HOLD_FILE       = Path.home() / ".config" / "agent_bridge" / "supervisor_hold"
EXIT_NOT_AUTHORISED = 78
MAX_EVENTS      = 20


@dataclass( frozen=True )
class Limits:
    start_grace_seconds:     float = 120.0    # a just-restarted worker hasn't written its state yet
    waiting_grace_seconds:   float = 180.0    # past next_check_at by this much: the loop is stuck
    working_silence_seconds: float = 1500.0   # no output while "working" (task timeout is ~11 min)
    max_restarts:            int   = 3        # per window
    window_seconds:          float = 3600.0


@dataclass( frozen=True )
class Observation:
    now:              float
    active_state:     str                 # systemd ActiveState: active, inactive, failed, activating...
    main_pid:         int
    exit_status:      int | None          # ExecMainStatus of the last exit
    active_since:     float | None        # epoch seconds the unit entered active
    command:          str                 # /proc/<pid>/cmdline, "" when unreadable
    poll_state:       dict | None
    last_output_at:   float | None        # epoch seconds of the unit's last journal line
    held:             bool = False


@dataclass( frozen=True )
class Verdict:
    status: str                           # healthy | starting | stalled | stopped | unauthorised | held
    reason: str
    restart: bool = False


def _epoch( text: object ) -> float | None:
    try:
        return datetime.fromisoformat( str( text )).timestamp()
    except ( TypeError, ValueError ):
        return None


def assess( o: Observation, limits: Limits = Limits()) -> Verdict:
    """Pure: what state the worker is in and whether a restart would help."""
    if o.held:
        return Verdict( "held", f"{ HOLD_FILE } exists; not supervising" )
    if o.active_state != "active":
        if o.exit_status == EXIT_NOT_AUTHORISED:
            return Verdict( "unauthorised", "the gateway rejected the worker token (exit 78); restarting won't help" )
        if o.active_state in ( "activating", "reloading" ):
            return Verdict( "starting", f"unit is { o.active_state }" )
        return Verdict( "stopped", f"unit is { o.active_state } (last exit { o.exit_status })", restart=True )
    if not o.main_pid or WORKER_SCRIPT not in o.command:
        return Verdict( "stopped", f"unit is active but PID { o.main_pid } is not { WORKER_SCRIPT }", restart=True )
    up_for = o.now - ( o.active_since or o.now )
    state  = o.poll_state or {}
    if state.get( "pid" ) != o.main_pid:
        if up_for <= limits.start_grace_seconds:
            return Verdict( "starting", f"worker PID { o.main_pid } up { up_for:.0f}s, no state yet" )
        return Verdict( "stalled", f"worker PID { o.main_pid } up { up_for:.0f}s but has never written its poll state",
                        restart=True )
    phase = state.get( "phase" )
    if phase == "waiting":
        due = _epoch( state.get( "next_check_at" ))
        if due is not None and o.now - due > limits.waiting_grace_seconds:
            return Verdict( "stalled", f"next check was due { o.now - due:.0f}s ago and hasn't happened", restart=True )
        return Verdict( "healthy", f"waiting; last check { state.get( 'outcome' ) }" )
    if phase == "working":
        quiet = o.now - ( o.last_output_at or o.active_since or o.now )
        if quiet > limits.working_silence_seconds:
            return Verdict( "stalled", f"working but silent for { quiet:.0f}s", restart=True )
        return Verdict( "healthy", f"working; last output { quiet:.0f}s ago" )
    return Verdict( "starting", f"poll state phase is { phase }" )


# -- ports --------------------------------------------------------------------
class IServiceManager( Protocol ):
    def show( self, unit: str ) -> dict[ str, str ]: ...
    def restart( self, unit: str ) -> tuple[ bool, str ]: ...
    def last_output_at( self, unit: str ) -> float | None: ...


class IHealthStore( Protocol ):
    def load( self ) -> dict: ...
    def save( self, report: dict ) -> None: ...


# -- adapters -----------------------------------------------------------------
class SystemdUserManager:
    """`systemctl --user` / `journalctl --user`, fixed argv, one unit name, no shell."""

    def _run( self, argv: list[ str ]) -> subprocess.CompletedProcess:
        return subprocess.run( argv, capture_output=True, text=True, timeout=60, check=False )

    def show( self, unit: str ) -> dict[ str, str ]:
        out = self._run([ "systemctl", "--user", "show", unit, "--property",
                          "ActiveState,LoadState,MainPID,ExecMainStatus,ActiveEnterTimestampMonotonic" ]).stdout
        return dict( line.split( "=", 1 ) for line in out.splitlines() if "=" in line )

    def restart( self, unit: str ) -> tuple[ bool, str ]:
        done = self._run([ "systemctl", "--user", "restart", unit ])
        return done.returncode == 0, ( done.stderr or done.stdout ).strip()[ :300 ]

    def last_output_at( self, unit: str ) -> float | None:
        out = self._run([ "journalctl", "--user", "-u", unit, "-n", "1", "-o", "json", "--no-pager" ]).stdout.strip()
        try:
            return int( json.loads( out.splitlines()[ -1 ])[ "__REALTIME_TIMESTAMP" ]) / 1e6
        except ( IndexError, KeyError, ValueError ):
            return None


class JsonHealthFile:
    def __init__( self, path: Path = HEALTH_FILE ) -> None:
        self._path = path

    def load( self ) -> dict:
        try:
            data = json.loads( self._path.read_text( encoding="utf-8" ))
            return data if isinstance( data, dict ) else {}
        except ( OSError, ValueError ):
            return {}

    def save( self, report: dict ) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        fd, tmp = tempfile.mkstemp( dir=self._path.parent, suffix=".tmp" )
        with os.fdopen( fd, "w", encoding="utf-8" ) as handle:
            json.dump( report, handle, indent=1 )
        os.chmod( tmp, 0o644 )
        os.replace( tmp, self._path )


def read_cmdline( pid: int ) -> str:
    if not pid:
        return ""
    try:
        return " ".join( p.decode( "utf-8", "replace" ) for p in Path( f"/proc/{ pid }/cmdline" ).read_bytes().split( b"\0" ) if p )
    except OSError:
        return ""


def read_json( path: Path ) -> dict | None:
    try:
        data = json.loads( path.read_text( encoding="utf-8" ))
        return data if isinstance( data, dict ) else None
    except ( OSError, ValueError ):
        return None


# -- the supervisor -----------------------------------------------------------
@dataclass
class WorkerSupervisor:
    services:  IServiceManager
    health:    IHealthStore
    observe_extra: Callable[[ int ], tuple[ str, dict | None, bool ]]   # pid -> (cmdline, poll_state, held)
    clock:     Callable[[], float ] = time.time
    monotonic: Callable[[], float ] = time.monotonic
    say:       Callable[[ str ], None ] = lambda line: print( line, flush=True )
    limits:    Limits = field( default_factory=Limits )
    unit:      str = UNIT

    def observe( self ) -> Observation:
        now  = self.clock()
        show = self.services.show( self.unit )
        pid  = int( show.get( "MainPID", "0" ) or 0 )
        mono = int( show.get( "ActiveEnterTimestampMonotonic", "0" ) or 0 )
        since = now - ( self.monotonic() - mono / 1e6 ) if mono else None
        status = show.get( "ExecMainStatus", "" )
        command, poll_state, held = self.observe_extra( pid )
        return Observation( now=now, active_state=show.get( "ActiveState", "unknown" ), main_pid=pid,
                            exit_status=int( status ) if status.lstrip( "-" ).isdigit() else None,
                            active_since=since, command=command, poll_state=poll_state,
                            last_output_at=self.services.last_output_at( self.unit ), held=held )

    def run_once( self, dry_run: bool = False ) -> dict:
        obs      = self.observe()
        verdict  = assess( obs, self.limits )
        previous = self.health.load()
        restarts = [ t for t in previous.get( "restarts", []) if obs.now - float( t ) < self.limits.window_seconds ]
        events   = list( previous.get( "events", []))[ -MAX_EVENTS: ]
        action   = "none"
        if verdict.restart and dry_run:
            action = "would restart (dry run)"
        elif verdict.restart and len( restarts ) >= self.limits.max_restarts:
            action = f"gave up: { len( restarts ) } restarts in the last { self.limits.window_seconds / 60:.0f} min"
        elif verdict.restart:
            ok, detail = self.services.restart( self.unit )
            restarts.append( obs.now )
            action = "restarted" if ok else f"restart failed: { detail }"
        stamp = datetime.fromtimestamp( obs.now ).astimezone().isoformat( timespec="seconds" )
        if action != "none" or verdict.status != previous.get( "status" ):
            line = f"{ verdict.status }: { verdict.reason }" + ( f" -> { action }" if action != "none" else "" )
            events.append({ "at": stamp, "status": verdict.status, "message": line })
            self.say( line )
        report = { "checked_at": stamp, "unit": self.unit, "status": verdict.status, "reason": verdict.reason,
                   "action": action, "pid": obs.main_pid or None, "unit_state": obs.active_state,
                   "restarts": restarts, "restarts_in_window": len( restarts ),
                   "max_restarts": self.limits.max_restarts, "events": events[ -MAX_EVENTS: ]}
        self.health.save( report )
        return report


def build_supervisor( lane: WorkerLane = WorkerLane()) -> WorkerSupervisor:
    """Composition root: one supervisor per worker lane, each with its own unit, poll-state and health file."""
    def extra( pid: int ) -> tuple[ str, dict | None, bool ]:
        return read_cmdline( pid ), read_json( lane.poll_state_path ), HOLD_FILE.exists()
    return WorkerSupervisor( SystemdUserManager(), JsonHealthFile( lane.health_path ), extra, unit=lane.unit )


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--dry-run", action="store_true", help="assess and report, never restart" )
    args   = parser.parse_args( argv )
    for lane in ( WorkerLane( name="general" ), WorkerLane( name="express" )):
        supervisor = build_supervisor( lane )
        if supervisor.services.show( supervisor.unit ).get( "LoadState" ) == "not-found":
            continue                                    # a lane that was never installed is not a failure
        report = supervisor.run_once( dry_run=args.dry_run )
        print( json.dumps({ "lane": lane.name, **{ k: report[ k ] for k in ( "status", "reason", "action", "pid", "restarts_in_window" )}}),
               flush=True )
    return 0


if __name__ == "__main__":
    sys.exit( main())

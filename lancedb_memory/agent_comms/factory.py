"""The composition root: the one module that names concrete classes.

`build_post_office()` wires the whole post office from a mailbox root and a
registry file. Everything else in the package -- and every caller outside it
(the receptionists' dispatch runner, the ADWs) -- holds only the interfaces
on the returned `PostOfficeKit`.

`RuntimeExecutorFactory` is the Abstract Factory: each card names a
`runtime`, and that runtime's builder makes the card's ITaskExecutor. A new
runtime is one entry in `builders`; a new agent on an existing runtime is
one entry in agents.json and no code at all.
"""
from __future__ import annotations

import os
import shlex
import shutil
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from object_trace.factory import trace_db_path

from .adapters.alert_board import FileAlertBoard
from .adapters.event_log import FanOutPublisher, JsonlEventLog
from .adapters.file_mailbox import FileMailboxTransport
from .adapters.json_registry import JsonAgentRegistry
from .adapters.keyword_router import KeywordRouter
from .adapters.launchers import DetachedWorkerLauncher, SystemClock, outside_unit_prefix
from .adapters.liveness import FileLockLiveness
from .alerts import CallerAlertForwarder, StalledTaskSweeper, UnrecoverableErrors
from .board import MailboxTaskBoard, TaskCanceller
from .executors.basic import ByTaskType, HelloWorldExecutor, ObservedExecutor
from .live_output import clean, event_name
from .outcome import JudgedExecutor
from agent_bridge_worker.redaction import SecretScanner
from .executors.claude_cli import ClaudeCliExecutor, ClaudeLoginLifetime, refresh_command
from .executors.claude_sdk import ClaudeSdkExecutor
from .executors.codex_cli import CodexCliExecutor
from .executors.jail import BwrapJail, WorkspacePrivatePaths
from .executors.mechanic import AdwSubprocessExecutor, DiagnosisExecutor
from .executors.object_debugger import ObjectDebuggerExecutor
from .executors.pi_coding_agent import PiCodingAgentExecutor, PiLoginLifetime, refresh_command as pi_refresh_command
from .executors.sheila_ops import (
    AcceptanceProbe, AdwEntry, BridgeOperation, HostProbe, HttpBridgeView, JailProbe, JsonlSheilaLedger, LoginProbe,
    MessageOperation, PiRuntimeProbe, RunAdwOperation, SheilaOperations, StatusOperation, SubprocessRunner, UnitProbe,
    WorkspaceProbe,
)
from .executors.sidecar import SidecarTurnExecutor
from .mechanic import FailureForwarder, JsonlCaseHistory, MailboxDiagnostic
from .models import AgentCard, Envelope, TaskEvent
from .ports import (
    IAgentDiagnostic, IAgentRegistry, IAlertBoard, ITaskLiveness, IClock, ICaseHistory, IEventLog, IEventPublisher, IMessageRouter,
    IMessageTransport, IRequestReply, IRequestValidator, ITaskBoard, ITaskDispatcher, ITaskExecutor, IWorkerLauncher,
)
from .post_office import PostOffice
from .request_reply import MailboxReplyWaiter
from .validation import AcceptsSender, HasCapability, NoLoopBack, RegisteredParties, ValidationChain
from .worker import AgentWorker, IdempotentRetries

PACKAGE      = Path( __file__ ).resolve().parent
WORKSPACE    = PACKAGE.parents[ 1 ]                       # /home/adamsl/agent_blocks
DEFAULT_ROOT = WORKSPACE / "adws" / "adw_data" / "post_office"
REGISTRY     = PACKAGE / "agents.json"
WORKER_ADW   = WORKSPACE / "adws" / "adw_op_execute_task.py"
MECHANIC_ADW = WORKSPACE / "adws" / "adw_toyota_mechanic.py"

ExecutorBuilder = Callable[ [ AgentCard ], ITaskExecutor ]


class RuntimeExecutorFactory:
    """IExecutorFactory."""

    def __init__( self, builders: dict[ str, ExecutorBuilder ], events: IEventPublisher ) -> None:
        self._builders = dict( builders )
        self._events   = events

    def executor_for( self, card: AgentCard ) -> ITaskExecutor:
        builder = self._builders.get( card.runtime )
        if builder is None:
            raise LookupError( f"no executor factory for runtime { card.runtime!r } (agent { card.name })" )
        return ObservedExecutor( builder( card ), self._events, card.name )


@dataclass
class PostOfficeKit:
    """What a caller gets: interfaces only (plus the root, for display)."""

    root:       Path
    registry:   IAgentRegistry
    transport:  IMessageTransport
    events:     IEventPublisher
    event_log:  IEventLog
    router:     IMessageRouter
    validator:  IRequestValidator
    dispatcher: ITaskDispatcher
    replies:    IRequestReply
    board:      ITaskBoard
    canceller:  TaskCanceller
    diagnostic: IAgentDiagnostic
    cases:      ICaseHistory
    executors:  RuntimeExecutorFactory
    launcher:   IWorkerLauncher
    alerts:     IAlertBoard
    liveness:   ITaskLiveness
    overrides:  dict[ str, ITaskExecutor ] = field( default_factory=dict )

    def worker_for( self, agent: str ) -> AgentWorker:
        card = self.registry.card( agent )
        if card is None:
            raise LookupError( f"no agent called { agent }" )
        executor = self.overrides.get( agent ) or self.executors.executor_for( card )
        return AgentWorker( agent, self.transport, JudgedExecutor( executor ), self.events,
                            UnrecoverableErrors( IdempotentRetries()), self.liveness )

    def sweeper( self, grace_seconds: float = 60.0 ) -> StalledTaskSweeper:
        """Settles tasks whose worker died mid-run, for every agent."""
        return StalledTaskSweeper( self.registry, self.transport, self.liveness, self.worker_for,
                                   self.launcher, grace_seconds )


def progress_sink( events: IEventPublisher | None, agent: str ):
    """The one place an agent's live steps are filtered and recorded."""
    def publish( envelope: Envelope, phase: str, message: str ) -> None:
        if events is not None:
            events.publish( TaskEvent( name=event_name( phase ), task_id=envelope.id, agent=agent,
                                       details={ "phase": phase,
                                                 "message": clean( message, SecretScanner().found )}))
    return publish


def default_builders( diagnostic: IAgentDiagnostic, cases: ICaseHistory, output_dir: Path,
                      events: IEventPublisher | None = None, dispatcher: ITaskDispatcher | None = None,
                      replies: IRequestReply | None = None ) -> dict[ str, ExecutorBuilder ]:
    from .executors.machine_ops import MachineOpsExecutor
    def claude_sdk( card: AgentCard ) -> ITaskExecutor:
        s = card.settings
        return ClaudeSdkExecutor( display_name=card.display_name, model=str( s[ "model" ]),
                                  tools=list( s.get( "tools", [ "Read", "Glob", "Grep" ])),
                                  workdir=str( s.get( "workdir", WORKSPACE )), output_dir=str( output_dir ),
                                  max_turns=int( s.get( "max_turns", 60 )))

    def sidecar( card: AgentCard ) -> ITaskExecutor:
        return SidecarTurnExecutor( card.display_name, str( card.settings[ "url" ]))

    def mechanic( card: AgentCard ) -> ITaskExecutor:
        repair = AdwSubprocessExecutor( MECHANIC_ADW, cwd=WORKSPACE,
                                        command=[ str( WORKSPACE / "lancedb_memory" / ".venv" / "bin" / "python" )])
        return ByTaskType({ "diagnose": DiagnosisExecutor( diagnostic, cases ), "repair": repair })

    def adw( card: AgentCard ) -> ITaskExecutor:
        """Any ADW that takes the request as its first argument and ends on a
        DONE:/REFUSED: line; the card says which script and how to run it."""
        s       = card.settings
        command = s.get( "command" )
        command = shlex.split( command ) if isinstance( command, str ) else [ str( c ) for c in command or []]
        return AdwSubprocessExecutor( WORKSPACE / str( s[ "script" ]), cwd=WORKSPACE,
                                      extra_args=[ str( a ) for a in s.get( "extra_args", [])],
                                      command=command or None, timeout_seconds=float( s.get( "timeout_seconds", 3600.0 )),
                                      diagnose_flag=s.get( "diagnose_flag" ), sender_flag=s.get( "sender_flag" ))

    def codex_cli( card: AgentCard ) -> ITaskExecutor:
        """Codex CLI, read-only sandbox fixed in the executor; the card picks where, how long, and the jail."""
        s    = card.settings
        home = codex_home( s.get( "codex_home" ))
        return CodexCliExecutor( Path( str( s.get( "workdir", WORKSPACE ))), binary=str( s.get( "binary", "codex" )),
                                 timeout_seconds=float( s.get( "timeout_seconds", 900.0 )),
                                 model=s.get( "model" ), jail=codex_jail( s.get( "jail" ), home ),
                                 secret_files=[ home / "auth.json" ], progress=progress_sink( events, card.name ),
                                 read_roots=[ Path( str( r )) for r in s.get( "read_roots", [])])

    def codex_cli_write( card: AgentCard ) -> ITaskExecutor:
        """Codex CLI with `--sandbox workspace-write`, jailed to the one workspace it is given.

        Only for cards whose capabilities say `writes: true`. The jail is
        required: a card that names none is refused here rather than run unconfined.
        """
        s    = card.settings
        home = codex_home( s.get( "codex_home" ))
        jail = codex_jail( s.get( "jail" ), home, root_writable=True )
        if jail is None:
            raise ValueError( f"{ card.name }: a write-enabled codex card must name a jail" )
        return CodexCliExecutor( Path( str( s[ "workdir" ])), binary=str( s.get( "binary", "codex" )),
                                 timeout_seconds=float( s.get( "timeout_seconds", 900.0 )),
                                 model=s.get( "model" ), jail=jail, secret_files=[ home / "auth.json" ],
                                 sandbox="workspace-write", progress=progress_sink( events, card.name ),
                                 write_roots=[ Path( str( r )) for r in s.get( "write_roots", [])])

    def codex_home( value: object ) -> Path:
        """The bridge's own CODEX_HOME: its own login, none of EG's Codex history."""
        home = Path( str( value or Path.home() / ".codex-bridge" )).expanduser()
        if home.resolve() == ( Path.home() / ".codex" ).resolve():
            raise ValueError( "the codex card must not use ~/.codex; it holds EG's own Codex history" )
        return home

    def codex_jail( name: object, home: Path, root_writable: bool = False ) -> BwrapJail | None:
        if name is None:
            return None
        if name != "bwrap":
            raise ValueError( f"unknown jail { name !r} on the codex card; the only one is 'bwrap'" )
        return BwrapJail( WorkspacePrivatePaths(), state_dirs=[ home ], env={ "CODEX_HOME": str( home )},
                          required=[ home / "auth.json" ], root_writable=root_writable )

    def claude_cli( card: AgentCard ) -> ITaskExecutor:
        """Claude Code CLI on EG's existing subscription login, jailed like Codex.

        One card, one executor per operation: each capability marked
        `writes: true` gets a writer jailed to the one workspace it is given;
        every other capability gets a reader. Both always run in the jail.
        """
        s       = card.settings
        home    = claude_home( s.get( "claude_home" ))
        login   = Path( str( s.get( "login_file", Path.home() / ".claude" / ".credentials.json" ))).expanduser()
        binary  = str( s.get( "binary", "claude" ))
        # What the jail sees of the login: a copy without the refresh token, remade before every run.
        jail_login = Path( str( s.get( "jail_login_file", Path.home() / ".cache" / "claude_bridge" / f"{ card.name }.credentials.json" ))).expanduser()
        shell   = bool( s.get( "shell", False ))
        common  = dict( binary=binary, model=str( s.get( "model", "sonnet" )), jail_login=jail_login,
                        timeout_seconds=float( s.get( "timeout_seconds", 900.0 )),
                        max_turns=int( s.get( "max_turns", 40 )), secret_files=[ login ],
                        login=ClaudeLoginLifetime( login, refresh_command( binary )),
                        progress=progress_sink( events, card.name ))

        def jail( writes: bool ) -> BwrapJail:
            return BwrapJail( WorkspacePrivatePaths(), state_dirs=[ home ], env={ "CLAUDE_CONFIG_DIR": str( home )},
                              required=[ login ], root_writable=writes,
                              file_binds=[( jail_login, home / ".credentials.json" )])

        # A build needs more room than a look: `write_*` budgets apply to writing capabilities only, and
        # default to the card's own. Failure triage found 1 implement task in 4 stopping at 40 turns.
        writer  = { **common, "timeout_seconds": float( s.get( "write_timeout_seconds", common[ "timeout_seconds" ])),
                    "max_turns": int( s.get( "write_max_turns", common[ "max_turns" ]))}

        executors: dict[ str, ITaskExecutor ] = {}
        for capability in card.capabilities:
            if capability.writes:
                executors[ capability.task_type ] = ClaudeCliExecutor(
                    Path( str( s[ "write_workdir" ])), writes=True, jail=jail( True ), shell=shell,
                    write_roots=[ Path( str( r )) for r in s.get( "write_roots", [])], **writer )
            else:
                executors[ capability.task_type ] = ClaudeCliExecutor(
                    Path( str( s.get( "workdir", WORKSPACE ))), writes=False, jail=jail( False ),
                    read_roots=[ Path( str( r )) for r in s.get( "read_roots", [])], **common )
        pi = ByTaskType( executors )
        if not isinstance( s.get( "operations" ), dict ):
            return pi
        operations, ledger = sheila_operations( card, lifetime.seconds_left, dispatcher, replies )
        return SheilaOperations( pi, operations, ledger )

    def claude_home( value: object ) -> Path:
        """The bridge's own CLAUDE_CONFIG_DIR: none of EG's Claude conversations or settings."""
        home = Path( str( value or Path.home() / ".claude-bridge" )).expanduser()
        if home.resolve() == ( Path.home() / ".claude" ).resolve():
            raise ValueError( "the claude card must not use ~/.claude; it holds EG's own Claude history" )
        home.mkdir( mode=0o700, parents=True, exist_ok=True )
        placeholder = home / ".credentials.json"          # where the jail binds the login, read-only
        if not placeholder.exists():
            placeholder.touch( mode=0o600 )
        return home

    def pi_coding_agent( card: AgentCard ) -> ITaskExecutor:
        """Pi Coding Agent (Sheila), jailed like Claude, on a pinned `provider/id` model.

        One executor per operation: each capability marked `writes: true` gets a writer jailed to the one
        workspace it is given; every other capability gets a reader. Both always run in the jail, which sees
        Pi's own state directory, Pi's install read-only, and a copy of the one provider login without its
        refresh token.
        """
        s        = card.settings
        source   = Path( str( s.get( "pi_source_home", Path.home() / ".pi" / "agent" ))).expanduser()
        home     = pi_home( s.get( "pi_home" ), source )
        login    = Path( str( s.get( "login_file", source / "auth.json" ))).expanduser()
        binary   = str( s[ "binary" ])
        model    = str( s[ "model" ])
        provider = model.split( "/", 1 )[ 0 ]
        install  = Path( str( s[ "install_dir" ])).expanduser()          # node + pi, bound read-only into the jail
        jail_login = Path( str( s.get( "jail_login_file", Path.home() / ".cache" / "pi_bridge" / f"{ card.name }.auth.json" ))).expanduser()
        node_bin = str( Path( binary ).parent )
        lifetime = PiLoginLifetime( login, provider, pi_refresh_command( binary, model ),
                                    env={ **os.environ, "PATH": f"{ node_bin }:{ os.environ.get( 'PATH', '' ) }" })
        common   = dict( binary=binary, model=model, jail_login=jail_login, login=lifetime, secret_files=[ login ],
                         timeout_seconds=float( s.get( "timeout_seconds", 900.0 )), progress=progress_sink( events, card.name ))

        def jail( writes: bool ) -> BwrapJail:
            return BwrapJail( WorkspacePrivatePaths(), state_dirs=[ home ],
                              env={ "PI_CODING_AGENT_DIR": str( home ), "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1",
                                    "PI_TELEMETRY": "0", "PATH": f"{ node_bin }:{ BwrapJail.PATH }" },
                              required=[ login, Path( binary )], root_writable=writes,
                              file_binds=[( jail_login, home / "auth.json" ), ( install, install )])

        executors: dict[ str, ITaskExecutor ] = {}
        for capability in card.capabilities:
            if capability.writes:
                executors[ capability.task_type ] = PiCodingAgentExecutor(
                    Path( str( s[ "write_workdir" ])), writes=True, shell=bool( s.get( "shell", False )), jail=jail( True ),
                    write_roots=[ Path( str( r )) for r in s.get( "write_roots", [])],
                    **{ **common, "timeout_seconds": float( s.get( "write_timeout_seconds", common[ "timeout_seconds" ])) })
            else:
                executors[ capability.task_type ] = PiCodingAgentExecutor(
                    Path( str( s.get( "workdir", WORKSPACE ))), writes=False, jail=jail( False ),
                    read_roots=[ Path( str( r )) for r in s.get( "read_roots", [])], **common )
        pi = ByTaskType( executors )
        if not isinstance( s.get( "operations" ), dict ):
            return pi
        operations, ledger = sheila_operations( card, lifetime.seconds_left, dispatcher, replies )
        return SheilaOperations( pi, operations, ledger )

    def pi_home( value: object, source: Path ) -> Path:
        """The bridge's own PI_CODING_AGENT_DIR: its own settings and model list, none of EG's Pi sessions."""
        home = Path( str( value or Path.home() / ".pi-bridge" / "agent" )).expanduser()
        if home.resolve() == source.resolve():
            raise ValueError( "the pi card must not use ~/.pi/agent; it holds EG's own Pi login and sessions" )
        home.mkdir( mode=0o700, parents=True, exist_ok=True )
        for name in ( "models.json", "settings.json" ):                 # seeded once, then the bridge's own
            if not ( home / name ).exists() and ( source / name ).is_file():
                shutil.copyfile( source / name, home / name )
        if not ( home / "bin" ).exists() and ( source / "bin" ).is_dir():      # Pi's fd and rg: offline, it cannot fetch them
            shutil.copytree( source / "bin", home / "bin" )
        placeholder = home / "auth.json"                                # where the jail binds the login, read-only
        if not placeholder.exists():
            placeholder.touch( mode=0o600 )
        return home

    return {
        "machine_ops": lambda card: MachineOpsExecutor(),
        "object_trace": lambda card: ObjectDebuggerExecutor( trace_db_path()),
        "claude_sdk":  claude_sdk,
        "claude_cli":  claude_cli,
        "codex_cli":   codex_cli,
        "pi_coding_agent": pi_coding_agent,
        "codex_cli_write": codex_cli_write,
        "adw":         adw,
        "sidecar":     sidecar,
        "mechanic":    mechanic,
        "hello_world": lambda card: HelloWorldExecutor(),
    }


def workspace_version( repo: Path = WORKSPACE ) -> str:
    """The checked-out commit, read from `.git` (no subprocess): what code Sheila's status is reporting from."""
    try:
        head = ( repo / ".git" / "HEAD" ).read_text( encoding="utf-8" ).strip()
        if head.startswith( "ref: " ):
            ref = head[ 5: ]
            loose = repo / ".git" / ref
            if loose.is_file():
                return loose.read_text( encoding="utf-8" ).strip()[ :12 ]
            for line in ( repo / ".git" / "packed-refs" ).read_text( encoding="utf-8" ).splitlines():
                if line.endswith( " " + ref ):
                    return line.split()[ 0 ][ :12 ]
            return "unknown"
        return head[ :12 ]
    except OSError:
        return "unknown"


def sheila_operations( card: AgentCard, seconds_left: Callable[[], float ] | None,
                       dispatcher: ITaskDispatcher | None, replies: IRequestReply | None ) -> tuple[ list, JsonlSheilaLedger ]:
    """Sheila's deterministic operations, from her card's `operations` block. `message` exists only when a
    dispatcher is wired (a post office); the recipients and ADWs are the card's fixed lists."""
    s      = card.settings
    o      = dict( s.get( "operations" ) or {})
    runner = SubprocessRunner()
    ledger = JsonlSheilaLedger( Path( str( o.get( "ledger", Path.home() / ".local" / "share" / "sheila" / "ledger.jsonl" ))).expanduser())
    host   = socket.gethostname()
    alias  = str( o.get( "machine", "" ))
    here   = alias if host.lower() == str( o.get( "hostname", "" )).lower() else f"{ host } (not { alias or 'her machine' })"
    write  = Path( str( s.get( "write_workdir", WORKSPACE ))).expanduser()
    probes = [ HostProbe(), PiRuntimeProbe( str( s[ "binary" ]), str( s[ "model" ]), runner ), JailProbe(),
               WorkspaceProbe( "sandbox", write ),
               AcceptanceProbe( WORKSPACE / str( o.get( "acceptance_file", "agent_bridge/docs/SHEILA_ACCEPTANCE.json" ))) ]
    if seconds_left is not None:
        probes.insert( 2, LoginProbe( seconds_left ))
    if o.get( "worker_unit" ):
        probes.append( UnitProbe( "worker", str( o[ "worker_unit" ]), runner ))
    token  = o.get( "bridge_token_file" )
    view   = HttpBridgeView( str( o.get( "bridge_url", "" )), Path( str( token )).expanduser() if token else None )
    adws   = { name: AdwEntry.model_validate( entry ) for name, entry in dict( o.get( "adws" ) or {}).items()}
    ops: list = [ StatusOperation( probes, ledger, here, workspace_version()), BridgeOperation( view ),
                  RunAdwOperation( adws, WORKSPACE, runner )]
    if dispatcher is not None and replies is not None:
        ops.append( MessageOperation( dispatcher, replies, { k: list( v ) for k, v in dict( o.get( "message_recipients" ) or {}).items()},
                                      wait_seconds=float( o.get( "message_wait_seconds", 120.0 )), sender=card.name ))
    return ops, ledger


def build_post_office( root: Path | None = None,
                       registry_path: Path | None = None,
                       launcher: IWorkerLauncher | None = None,
                       clock: IClock | None = None,
                       builders: dict[ str, ExecutorBuilder ] | None = None,
                       forward_failures_to_mechanic: bool = True ) -> PostOfficeKit:
    root      = Path( root or os.getenv( "AGENT_POST_OFFICE_ROOT" ) or DEFAULT_ROOT )
    registry  = JsonAgentRegistry( registry_path or REGISTRY )
    transport = FileMailboxTransport( root / "mailboxes" )
    event_log = JsonlEventLog( root / "events.jsonl" )
    events    = FanOutPublisher([ event_log ])
    cases     = JsonlCaseHistory( root / "mechanic_cases.jsonl" )
    sessions  = [ WORKSPACE / "adws" / "adw_data" / "sessions",
                  Path( "/home/adamsl/rol_finances/adws/adw_data/sessions" )]
    diagnostic = MailboxDiagnostic( transport, event_log, cases, sessions )
    replies    = MailboxReplyWaiter( transport, clock or SystemClock())
    venv       = WORKSPACE / "lancedb_memory" / ".venv" / "bin" / "python"
    launcher   = launcher or DetachedWorkerLauncher( WORKER_ADW, cwd=WORKSPACE, log_dir=root / "worker_logs",
                                                     python=str( venv ) if venv.exists() else None,
                                                     skip=frozenset({ "post-office" }),
                                                     prefix=outside_unit_prefix())
    validator  = ValidationChain([ RegisteredParties( registry ), AcceptsSender( registry ),
                                   HasCapability( registry ), NoLoopBack( transport )])
    dispatcher = PostOffice( registry, validator, transport, events, launcher, replies )
    alerts     = FileAlertBoard( root / "alerts" )
    # Before the mechanic: the caller hears first, whatever the mechanic does.
    events.subscribe( CallerAlertForwarder( alerts, transport, registry ))
    if forward_failures_to_mechanic:
        events.subscribe( FailureForwarder( dispatcher ))
    executors = RuntimeExecutorFactory(
        builders or default_builders( diagnostic, cases, root / "reports", events, dispatcher, replies ), events )
    return PostOfficeKit(
        root=root, registry=registry, transport=transport, events=events, event_log=event_log,
        router=KeywordRouter( registry, repositories=registry.repositories ), validator=validator, dispatcher=dispatcher, replies=replies,
        board=MailboxTaskBoard( transport ), canceller=TaskCanceller( transport, events ),
        diagnostic=diagnostic, cases=cases, executors=executors, launcher=launcher,
        alerts=alerts, liveness=FileLockLiveness( root / "running" ),
    )

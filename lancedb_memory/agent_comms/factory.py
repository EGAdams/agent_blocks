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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.event_log import FanOutPublisher, JsonlEventLog
from .adapters.file_mailbox import FileMailboxTransport
from .adapters.json_registry import JsonAgentRegistry
from .adapters.keyword_router import KeywordRouter
from .adapters.launchers import DetachedWorkerLauncher, SystemClock
from .board import MailboxTaskBoard, TaskCanceller
from .executors.basic import ByTaskType, HelloWorldExecutor, ObservedExecutor
from .executors.claude_sdk import ClaudeSdkExecutor
from .executors.mechanic import AdwSubprocessExecutor, DiagnosisExecutor
from .executors.sidecar import SidecarTurnExecutor
from .mechanic import FailureForwarder, JsonlCaseHistory, MailboxDiagnostic
from .models import AgentCard
from .ports import (
    IAgentDiagnostic, IAgentRegistry, IClock, ICaseHistory, IEventLog, IEventPublisher, IMessageRouter,
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
    overrides:  dict[ str, ITaskExecutor ] = field( default_factory=dict )

    def worker_for( self, agent: str ) -> AgentWorker:
        card = self.registry.card( agent )
        if card is None:
            raise LookupError( f"no agent called { agent }" )
        executor = self.overrides.get( agent ) or self.executors.executor_for( card )
        return AgentWorker( agent, self.transport, executor, self.events, IdempotentRetries())


def default_builders( diagnostic: IAgentDiagnostic, cases: ICaseHistory, output_dir: Path ) -> dict[ str, ExecutorBuilder ]:
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
                                        python=str( WORKSPACE / "lancedb_memory" / ".venv" / "bin" / "python" ))
        return ByTaskType({ "diagnose": DiagnosisExecutor( diagnostic, cases ), "repair": repair })

    return {
        "claude_sdk":  claude_sdk,
        "sidecar":     sidecar,
        "mechanic":    mechanic,
        "hello_world": lambda card: HelloWorldExecutor(),
    }


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
                                                     skip=frozenset({ "post-office" }))
    validator  = ValidationChain([ RegisteredParties( registry ), AcceptsSender( registry ),
                                   HasCapability( registry ), NoLoopBack( transport )])
    dispatcher = PostOffice( registry, validator, transport, events, launcher, replies )
    if forward_failures_to_mechanic:
        events.subscribe( FailureForwarder( dispatcher ))
    executors = RuntimeExecutorFactory(
        builders or default_builders( diagnostic, cases, root / "reports" ), events )
    return PostOfficeKit(
        root=root, registry=registry, transport=transport, events=events, event_log=event_log,
        router=KeywordRouter( registry, repositories=registry.repositories ), validator=validator, dispatcher=dispatcher, replies=replies,
        board=MailboxTaskBoard( transport ), canceller=TaskCanceller( transport, events ),
        diagnostic=diagnostic, cases=cases, executors=executors, launcher=launcher,
    )

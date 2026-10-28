"""No silent failures: the caller hears about every failed task, at once.

Before this, a failed task produced a failure reply in the sender's inbox and
a diagnosis for the mechanic -- and nobody read either. Sydney's four plans
for Opus failed that way, one after another, while she told the user Opus was
"working on it".

Three pieces, each behind an interface the rest of the package already has:

  CallerAlertForwarder  IEventSubscriber: every `task.failed` raises an Alert
                        on the IAlertBoard for whoever sent the task. The
                        receptionists' sidecars read that board; the dashboard
                        shows, speaks and notifies it.
  UnrecoverableErrors   IRetryPolicy Decorator: an error no retry can fix
                        (the model is refused, the login is bad, the credit
                        is gone) fails on the first attempt instead of the
                        third, so the alert comes three runs sooner.
  StalledTaskSweeper    settles tasks whose worker died mid-run (the worker
                        holds an ITaskLiveness lock while it executes), so a
                        killed worker becomes a retry or a loud failure
                        instead of a task "in progress" forever.
"""
from __future__ import annotations

import re

from .models import Alert, Envelope, TaskEvent, TaskResult
from .ports import IAgentRegistry, IAlertBoard, IMessageTransport, IRetryPolicy, ITaskLiveness, IWorkerLauncher

# Provider answers that say "this request can never succeed as sent".
# 408 and 429 are deliberately absent: a timeout or a rate limit can clear.
UNRECOVERABLE = re.compile(
    r"API Error: 4(0[0-79]|1\d|2[0-8]|[3-9]\d)\b|does not support this model|version [\d.]+ or newer is required|"
    r"invalid[ _-]api[ _-]key|authentication|not logged in|please run /login|credit balance|"
    r"permission_error|not_found_error|no executor factory",
    re.IGNORECASE )


class CallerAlertForwarder:
    """IEventSubscriber: a failed task raises an alert for the agent who sent it.

    Tasks the post office sent on its own behalf (a failure handed to the
    mechanic) have no caller to tell, so their failures go to `escalate_to`
    -- the receptionist who looks after the mechanic.
    """

    def __init__( self, board: IAlertBoard, transport: IMessageTransport, registry: IAgentRegistry,
                  internal: frozenset[ str ] = frozenset({ "post-office" }), escalate_to: str = "sydney" ) -> None:
        self._board       = board
        self._transport   = transport
        self._registry    = registry
        self._internal    = internal
        self._escalate_to = escalate_to

    def notify( self, event: TaskEvent ) -> None:
        if event.name != "task.failed":
            return
        found  = self._transport.find( event.task_id )
        sender = found[ 2 ].sender if found else None
        if sender is None:
            sender = self._escalate_to
        elif sender in self._internal:
            sender = self._escalate_to
        task_type = found[ 2 ].task_type if found else "task"
        card      = self._registry.card( event.agent )
        self._board.raise_alert( Alert(
            id        = event.task_id,
            agent     = sender,
            task_id   = event.task_id,
            recipient = card.display_name if card else event.agent,
            task_type = task_type,
            error     = str( event.details.get( "error" ) or "no error recorded" ),
            attempts  = int( event.details.get( "attempts" ) or 0 ),
        ))


class UnrecoverableErrors:
    """IRetryPolicy Decorator: never retry what cannot succeed, else ask the inner policy."""

    def __init__( self, inner: IRetryPolicy, pattern: re.Pattern[ str ] = UNRECOVERABLE ) -> None:
        self._inner   = inner
        self._pattern = pattern

    def should_retry( self, envelope: Envelope, result: TaskResult ) -> bool:
        if self._pattern.search( result.text or "" ):
            return False
        return self._inner.should_retry( envelope, result )


class StalledTaskSweeper:
    """Settle every claimed task whose worker is gone, for every agent.

    `grace_seconds` covers the instant between a claim and the worker taking
    its liveness lock. A recovered idempotent task is requeued and its agent
    woken; anything else fails -- and that failure raises an alert.
    """

    def __init__( self, registry: IAgentRegistry, transport: IMessageTransport, liveness: ITaskLiveness,
                  worker_for, launcher: IWorkerLauncher, grace_seconds: float = 60.0 ) -> None:
        self._registry  = registry
        self._transport = transport
        self._liveness  = liveness
        self._worker_for = worker_for
        self._launcher  = launcher
        self._grace     = grace_seconds

    def sweep( self ) -> list[ Envelope ]:
        settled = []
        for agent in self._registry.names():
            if not any( not self._liveness.is_alive( e.id ) for e in self._transport.box( agent, "in_process" )):
                continue
            try:
                recovered = self._worker_for( agent ).recover( self._grace )
            except LookupError:
                continue
            if any( e.status.value == "queued" for e in recovered ):
                self._launcher.wake( agent )
            settled.extend( recovered )
        return settled

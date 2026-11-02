"""IAgentAdapter over `agent_comms`' post office.

The bridge is one more sender (`chatgpt-bridge`) in the post office, so every
existing rule applies unchanged: the recipient's card must accept that sender,
it must declare the task type, and its own runtime (`codex_cli` runs
`codex exec --sandbox read-only`) decides how the work is done. The post
office wakes the recipient's worker itself; this adapter only sends, polls
its own inbox for the reply, and withdraws the task if the client cancels.

The post-office pieces are typed by the small Protocols below rather than
imported, so this package never depends on `agent_comms`' concretes.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..models import AgentOutcome, ClaimedTask, OutcomeKind
from ..ports import IClock, Signal


class ISender( Protocol ):
    def send( self, request: Any ) -> Any: ...                 # TaskRequest -> SendReceipt


class IReplyInbox( Protocol ):
    def take( self, sender: str, correlation_id: str ) -> Any | None: ...   # -> Envelope | None


class ICanceller( Protocol ):
    def cancel( self, task_id: str, by: str ) -> str: ...


class ITaskLookup( Protocol ):
    def task( self, task_id: str ) -> Any | None: ...          # -> Envelope | None


class IEventLog( Protocol ):
    def events_for( self, task_id: str ) -> list[ Any ]: ...   # -> list[TaskEvent]


class PostOfficeAgent:
    """Hands a claimed task to one post-office agent and waits for its reply."""

    def __init__( self, sender_name: str, sender: ISender, replies: IReplyInbox, canceller: ICanceller,
                  board: ITaskLookup, events: IEventLog, make_request: Callable[..., Any ], clock: IClock,
                  timeout_seconds: float, poll_seconds: float = 1.0 ) -> None:
        self._me       = sender_name
        self._sender   = sender
        self._replies  = replies
        self._cancel   = canceller
        self._board    = board
        self._events   = events
        self._request  = make_request          # agent_comms.models.TaskRequest, injected
        self._clock    = clock
        self._timeout  = timeout_seconds
        self._poll     = poll_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        receipt = self._sender.send( self._request(
            sender=self._me, recipient=task.agent, task_type=task.operation, text=task.prompt,
            context={ "workdir": str( workdir ), "bridge_task_id": task.task_id }, wait_seconds=0 ))
        if not receipt.accepted:
            return AgentOutcome( kind=OutcomeKind.REFUSED, summary=f"the post office refused it: { receipt.reason }" )
        local = receipt.task_id
        start = self._clock.monotonic()
        while True:
            reply = self._replies.take( self._me, local )
            if reply is not None:
                return self._outcome( reply, local )
            now = self._clock.monotonic()
            if now - start >= self._timeout:
                self._cancel.cancel( local, self._me )
                return AgentOutcome( kind=OutcomeKind.FAILED, local_task_id=local,
                                     summary=f"{ task.agent } did not answer within { round( self._timeout ) } seconds" )
            signal = keep_alive()                # cheap: the caller decides when it is time to heartbeat
            if signal == "lost":
                self._cancel.cancel( local, self._me )
                return AgentOutcome( kind=OutcomeKind.LEASE_LOST, local_task_id=local )
            if signal == "cancel":
                return self._withdraw( task, local )
            self._clock.sleep( self._poll )

    def lines( self, local_task_id: str ) -> list[ str ]:
        """ITaskEventSource: event names and times only -- never the details, which can carry prompt text."""
        return [ f"{ e.name } ({ e.agent }) at { e.at.isoformat( timespec='seconds' ) }"
                 for e in self._events.events_for( local_task_id )]

    def _withdraw( self, task: ClaimedTask, local: str ) -> AgentOutcome:
        self._cancel.cancel( local, self._me )
        envelope = self._board.task( local )
        status   = getattr( getattr( envelope, "status", None ), "value", "" )
        note     = ( "withdrawn before it started" if status == "cancelled"
                     else f"{ task.agent } had already started, so it may still finish on this machine; its answer will not be published" )
        return AgentOutcome( kind=OutcomeKind.CANCELLED, local_task_id=local, summary=f"cancelled: { note }" )

    @staticmethod
    def _outcome( reply: Any, local: str ) -> AgentOutcome:
        outcome   = getattr( reply.outcome, "value", str( reply.outcome ))
        performer = reply.performer or ""
        if outcome == "succeeded":
            body = reply.result or ""
            first = next(( line.strip() for line in body.splitlines() if line.strip()), "" )
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, body=body, performer=performer,
                                 summary=first[ :300 ], local_task_id=local )
        if outcome == "cancelled":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, summary="cancelled on this machine", local_task_id=local )
        return AgentOutcome( kind=OutcomeKind.FAILED, body=reply.error or "", performer=performer,
                             summary=( reply.error or "the agent failed" )[ :300 ], local_task_id=local )

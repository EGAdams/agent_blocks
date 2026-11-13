"""IAgentAdapter over `agent_comms`' post office.

The bridge is one more sender (`chatgpt-bridge`) in the post office, so every
existing rule applies unchanged: the recipient's card must accept that sender,
it must declare the task type, and its own runtime (`codex_cli` runs
`codex exec --sandbox read-only`) decides how the work is done. The post
office wakes the recipient's worker itself; this adapter only sends, polls
its own inbox for the reply, and withdraws the task if the client cancels.

Progress: an agent that reports its steps as it works (Claude publishes a
`task.progress` event per tool call, see agent_comms/executors/claude_cli.py)
has each new step passed to `relay` while the task runs -- the worker sends
them to the gateway as `progress` events -- instead of only in the audit
trail at the end. Only steps the agent itself recorded are relayed.

The post-office pieces are typed by the small Protocols below rather than
imported, so this package never depends on `agent_comms`' concretes.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..models import AgentOutcome, ClaimedTask, OutcomeKind
from ..ports import IClock, Signal
from ..senders import APPROVED_BRIDGE_SENDERS


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


PROGRESS    = "task.progress"
RELAY_EVERY = 3.0          # seconds between reads of the event log while a task runs


def progress_line( event: Any ) -> str:
    details = getattr( event, "details", {}) or {}
    phase   = str( details.get( "phase", "step" ))
    message = " ".join( str( details.get( "message", "" )).split())[ :200 ]
    return f"{ event.agent } { phase }: { message } at { event.at.isoformat( timespec='seconds' ) }"


class PostOfficeAgent:
    """Hands a claimed task to one post-office agent and waits for its reply."""

    def __init__( self, sender_name: str, sender: ISender, replies: IReplyInbox, canceller: ICanceller,
                  board: ITaskLookup, events: IEventLog, make_request: Callable[..., Any ], clock: IClock,
                  timeout_seconds: float, poll_seconds: float = 1.0,
                  relay: Callable[[ ClaimedTask, str ], None ] | None = None, max_relayed: int = 80 ) -> None:
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
        self._relay    = relay
        self._max      = max_relayed
        self._relayed: dict[ str, int ] = {}

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        sender = task.sender
        if sender not in APPROVED_BRIDGE_SENDERS:
            return AgentOutcome( kind=OutcomeKind.REFUSED, summary="gateway sender is not approved" )
        receipt = self._sender.send( self._request(
            sender=sender, recipient=task.agent, task_type=task.operation, text=task.prompt,
            context={ "workdir": str( workdir ), "bridge_task_id": task.task_id }, wait_seconds=0 ))
        if not receipt.accepted:
            return AgentOutcome( kind=OutcomeKind.REFUSED, summary=f"the post office refused it: { receipt.reason }" )
        local = receipt.task_id
        start = self._clock.monotonic()
        next_relay = start + RELAY_EVERY
        while True:
            reply = self._replies.take( sender, local )
            if reply is not None or self._clock.monotonic() >= next_relay:
                self._relay_progress( task, local )
                next_relay = self._clock.monotonic() + RELAY_EVERY
            if reply is not None:
                self._relayed.pop( local, None )
                outcome = self._outcome( reply, local )
                # Replies predate structured runtime fields. Read only a small
                # numeric/identifier allowlist from the existing audit event.
                details = self._runtime_details( local )
                return outcome.model_copy( update={ "details": details or None })
            now = self._clock.monotonic()
            if now - start >= self._timeout:
                self._cancel.cancel( local, sender )
                return AgentOutcome( kind=OutcomeKind.FAILED, local_task_id=local,
                                     summary=f"{ task.agent } did not answer within { round( self._timeout ) } seconds" )
            signal = keep_alive()                # cheap: the caller decides when it is time to heartbeat
            if signal == "lost":
                self._cancel.cancel( local, sender )
                return AgentOutcome( kind=OutcomeKind.LEASE_LOST, local_task_id=local )
            if signal == "cancel":
                return self._withdraw( task, local )
            self._clock.sleep( self._poll )

    def lines( self, local_task_id: str ) -> list[ str ]:
        """ITaskEventSource: event names and times only -- never the details, which can carry prompt text.

        Progress steps are the exception: their one message is written to be
        shown. When they were relayed live they are left out here, so the
        gateway does not get them twice.
        """
        out = []
        for e in self._events.events_for( local_task_id ):
            if e.name == PROGRESS:
                if self._relay is None:
                    out.append( progress_line( e ))
                continue
            out.append( f"{ e.name } ({ e.agent }) at { e.at.isoformat( timespec='seconds' ) }" )
        return out

    def _relay_progress( self, task: ClaimedTask, local: str ) -> None:
        if self._relay is None:
            return
        try:
            steps = [ e for e in self._events.events_for( local ) if e.name == PROGRESS ]
        except Exception:                               # noqa: BLE001 -- progress never stops the task
            return
        done = self._relayed.get( local, 0 )
        for event in steps[ done:self._max ]:
            try:
                self._relay( task, progress_line( event ))
            except Exception:                           # noqa: BLE001 -- best effort, like every gateway event
                pass
        self._relayed[ local ] = max( done, min( len( steps ), self._max ))

    def _withdraw( self, task: ClaimedTask, local: str ) -> AgentOutcome:
        self._cancel.cancel( local, task.sender )
        envelope = self._board.task( local )
        status   = getattr( getattr( envelope, "status", None ), "value", "" )
        note     = ( "withdrawn before it started" if status == "cancelled"
                     else f"{ task.agent } had already started, so it may still finish on this machine; its answer will not be published" )
        return AgentOutcome( kind=OutcomeKind.CANCELLED, local_task_id=local, summary=f"cancelled: { note }" )

    def _runtime_details( self, local: str ) -> dict:
        allowed = { "exit_code", "turns", "seconds", "subtype", "error_source", "model",
                    "configured_max_turns", "timeout_seconds", "child_pid", "process_group_id",
                    "timed_out", "bridge_task_id", "local_task_id" }
        try:
            records = self._events.events_for( local )
        except Exception:  # telemetry cannot stop delivery of a real result
            return {}
        for event in reversed( records ):
            if getattr( event, "name", "" ) not in ( "task.executed", "task.failed" ):
                continue
            raw = getattr( event, "details", {}) or {}
            found = { k: v for k, v in raw.items() if k in allowed
                      and isinstance( v, (str, int, float, bool)) }
            if found and ( "subtype" in found or "configured_max_turns" in found ):
                return found
        return {}

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

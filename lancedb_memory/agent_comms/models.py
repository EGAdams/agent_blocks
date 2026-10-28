"""Values that cross a boundary in the agent post office. Data only, no behaviour.

Every message between two agents is an `Envelope`: who sent it, who it is
for, what kind of work, the payload, a correlation id, and an audit trail of
lifecycle timestamps. It is Swarm-Forge's handoff file (headers + an opaque
body) translated into a Pydantic model, so a malformed message fails at the
boundary instead of halfway through a worker.

See README.md for the full design.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now( timezone.utc )


class TaskStatus( str, Enum ):
    """Where one task is in its lifecycle. `state.py` owns the transitions."""

    QUEUED    = "queued"      # in the recipient's inbox/new
    RUNNING   = "running"     # claimed: inbox/in_process
    SUCCEEDED = "succeeded"   # inbox/completed, a reply was sent
    FAILED    = "failed"      # inbox/failed, a failure reply was sent
    CANCELLED = "cancelled"   # withdrawn before anybody claimed it
    TIMED_OUT = "timed_out"   # only ever a caller's view: the task itself keeps going


class MessageKind( str, Enum ):
    REQUEST = "request"   # please do this work
    REPLY   = "reply"     # here is the result of a request you sent
    NOTE    = "note"      # one line for the recipient's information; no reply expected


class Capability( BaseModel ):
    """One kind of work an agent really can do, and what it needs to do it."""

    model_config = ConfigDict( extra="forbid" )

    task_type:   str                        # "build", "plan", "ask", "diagnose", "message", ...
    description: str
    writes:      bool = False               # may this work change files?
    idempotent:  bool = True                # safe to run twice? decides retries
    default_wait_seconds: float = 0.0       # 0 = asynchronous by default
    synonyms:    list[ str ] = Field( default_factory=list )   # other names a router may infer ("fix" for "repair")

    def answers_to( self, task_type: str ) -> bool:
        return task_type == self.task_type or task_type in self.synonyms


class AgentCard( BaseModel ):
    """A registered agent: who it is, what runs it, what it can do."""

    model_config = ConfigDict( extra="forbid" )

    name:         str = Field( pattern=r"^[a-z][a-z0-9-]*$" )   # no underscores: they separate filename fields
    display_name: str
    runtime:      str                       # which executor factory builds it: "claude_sdk", "sidecar", ...
    settings:     dict[ str, Any ] = Field( default_factory=dict )   # runtime-specific: model, url, tools ...
    capabilities: list[ Capability ] = Field( default_factory=list )
    accepts_from: list[ str ] = Field( default_factory=lambda: [ "*" ])   # authorised senders

    def capability( self, task_type: str ) -> Capability | None:
        return next(( c for c in self.capabilities if c.answers_to( task_type )), None )


class TaskRequest( BaseModel ):
    """What a sender asks for, before the post office addresses it."""

    model_config = ConfigDict( extra="forbid" )

    sender:     str
    recipient:  str
    task_type:  str
    text:       str = Field( min_length=1 )
    context:    dict[ str, Any ] = Field( default_factory=dict )
    priority:   int = Field( default=50, ge=0, le=99 )   # lower runs first, as in Swarm-Forge
    wait_seconds: float | None = None                    # None = the capability's default
    parent_id:  str | None = None                        # the task this one was spawned from


class Envelope( BaseModel ):
    """One message in the post office, with its audit trail."""

    model_config = ConfigDict( extra="forbid" )

    id:             str
    kind:           MessageKind
    sender:         str
    recipient:      str
    task_type:      str
    text:           str
    context:        dict[ str, Any ] = Field( default_factory=dict )
    priority:       int = 50
    correlation_id: str                      # the request's id; a reply carries it back
    parent_id:      str | None = None
    trace:          list[ str ] = Field( default_factory=list )   # agents this work has passed through
    idempotent:     bool = True
    max_attempts:   int = 1
    attempts:       int = 0
    status:         TaskStatus = TaskStatus.QUEUED
    outcome:        TaskStatus | None = None # on a reply: how the request went (status is the reply's own box)
    result:         str | None = None        # on a reply: the answer
    error:          str | None = None        # on a failed reply: what went wrong
    performer:      str | None = None        # which implementation did the work
    created_at:     datetime = Field( default_factory=utc_now )
    enqueued_at:    datetime | None = None
    dequeued_at:    datetime | None = None
    completed_at:   datetime | None = None


class TaskResult( BaseModel ):
    """What an executor hands back for one request."""

    model_config = ConfigDict( extra="forbid" )

    ok:        bool
    text:      str
    performer: str                           # e.g. "claude_sdk:claude-sonnet-5"
    cost_usd:  float | None = None
    details:   dict[ str, Any ] = Field( default_factory=dict )


class SendReceipt( BaseModel ):
    """What a sender gets back the moment a request is posted."""

    model_config = ConfigDict( extra="forbid" )

    accepted:  bool
    task_id:   str | None = None
    reason:    str = ""                      # why it was refused, in plain words
    waited:    bool = False
    reply:     Envelope | None = None        # set when the sender waited and an answer came


class TaskEvent( BaseModel ):
    """One lifecycle event, published to every subscriber and the event log."""

    model_config = ConfigDict( extra="forbid" )

    name:      str                           # "task.sent", "task.claimed", "task.failed", ...
    task_id:   str
    agent:     str                           # whose action it was
    at:        datetime = Field( default_factory=utc_now )
    details:   dict[ str, Any ] = Field( default_factory=dict )


class Diagnosis( BaseModel ):
    """Everything the mechanic needs about one failure, gathered by code."""

    model_config = ConfigDict( extra="forbid" )

    task_id:      str
    found:        bool
    sender:       str | None = None
    recipient:    str | None = None
    task_type:    str | None = None
    request_text: str | None = None
    context:      dict[ str, Any ] = Field( default_factory=dict )
    performer:    str | None = None
    attempts:     int = 0
    error:        str | None = None
    failed_phase: str | None = None          # for an ADW run: the phase that failed
    adw_id:       str | None = None
    events:       list[ TaskEvent ] = Field( default_factory=list )
    similar_cases: list[ str ] = Field( default_factory=list )   # earlier case ids with the same error

    def summary( self ) -> str:
        if not self.found:
            return f"No record of task { self.task_id }."
        where = f" in phase { self.failed_phase }" if self.failed_phase else ""
        seen  = f" Seen before: { ', '.join( self.similar_cases ) }." if self.similar_cases else ""
        if self.sender is None:   # an ADW run the post office never carried
            what = f"ADW run { self.adw_id }" if self.adw_id else f"Task { self.task_id }"
            return f"{ what } failed{ where } on { self.performer or 'an unknown agent' }: { _sentence( self.error ) }{ seen }"
        return ( f"Task { self.task_id } ({ self.task_type }, { self.sender } -> { self.recipient }) "
                 f"failed{ where } after { self.attempts } attempt(s) on { self.performer or 'nobody' }: "
                 f"{ _sentence( self.error ) }{ seen }" )


def _sentence( text: str | None ) -> str:
    text = ( text or "no error recorded" ).strip()
    return text if text.endswith(( ".", "!", "?" )) else text + "."

"""What crosses the object-trace boundary: an object, one event about it, and the derived view of both.

A `TraceObject` is the record a component wrote about itself when it was made.
A `TraceEvent` is one thing that happened to it afterwards. Neither says the
object is alive -- `ObjectView` does that, and only by comparing the last
event with the clock at the moment it is read (see `liveness.py`).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ID = r"^[A-Za-z0-9_.:@/-]{1,200}$"

State = Literal[ "created", "running", "waiting", "succeeded", "failed", "cancelled", "construct_failed" ]
TERMINAL: frozenset[ str ] = frozenset({ "succeeded", "failed", "cancelled", "construct_failed" })

EventKind = Literal[ "created", "construct_failed", "state", "heartbeat", "progress", "waiting",
                     "input", "output", "error", "succeeded", "failed", "cancelled", "telemetry" ]

Display = Literal[ "created", "heartbeat", "progress", "waiting", "failed", "stale", "succeeded", "cancelled" ]


class TraceObject( BaseModel ):
    """One instrumented object, as it described itself when it was made."""

    model_config = ConfigDict( extra="forbid" )

    object_id:       str = Field( pattern=ID )
    kind:            str = Field( pattern=r"^[a-z0-9_.]{1,60}$" )     # e.g. "bridge.task_run", "toyota.turn"
    interface:       str = Field( default="", max_length=120 )        # the Protocol it is written against
    implementation:  str = Field( default="", max_length=120 )        # the concrete class
    module:          str = Field( default="", max_length=200 )
    label:           str = Field( default="", max_length=200 )
    created_at:      float
    host:            str = Field( default="", max_length=100 )
    pid:             int | None = None
    process_session: str = Field( default="", max_length=64 )         # one id per process start
    run_id:          str | None = Field( default=None, pattern=ID )   # Toyota's request_id, or the worker's own
    task_id:         str | None = Field( default=None, pattern=ID )
    adw_id:          str | None = Field( default=None, pattern=ID )
    phase:           str | None = Field( default=None, max_length=80 )
    attempt:         int | None = None
    span_id:         str | None = Field( default=None, pattern=ID )
    parent_id:       str | None = Field( default=None, pattern=ID )
    deps:            list[ str ] = Field( default_factory=list, max_length=20 )
    heartbeat_seconds: float | None = None                            # how often it promises to speak while running
    state:           State = "created"
    inputs:          dict | None = None                               # redacted and bounded before it gets here
    outputs:         dict | None = None
    error:           dict | None = None
    last_event_at:   float | None = None
    last_event_kind: str | None = None


class TraceEvent( BaseModel ):
    """One thing that happened to an object. `event_id` makes a resend harmless."""

    model_config = ConfigDict( extra="forbid" )

    event_id:  str = Field( pattern=ID )
    object_id: str = Field( pattern=ID )
    at:        float
    kind:      EventKind
    state:     State | None = None
    message:   str = Field( default="", max_length=500 )
    data:      dict | None = None
    run_id:    str | None = Field( default=None, pattern=ID )
    task_id:   str | None = Field( default=None, pattern=ID )
    seq:       int | None = None                                      # assigned by the store, never by a writer


class ObjectView( BaseModel ):
    """An object as of the moment it was read: the record, its derived display state, and why."""

    model_config = ConfigDict( extra="forbid" )

    object:      TraceObject
    display:     Display
    alive:       bool | None                                          # None: nothing can say either way
    age_seconds: float | None                                         # since the last event
    reason:      str
    events:      list[ TraceEvent ] = Field( default_factory=list )
    dependents:  list[ str ] = Field( default_factory=list )


class Evidence( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    source: str                                                       # "object_trace", "sssf", "bridge"
    ref:    str                                                       # an object id, event seq, task id
    at:     float | None = None
    fact:   str = Field( max_length=400 )


class Answer( BaseModel ):
    """One inspection question answered with evidence -- or with what is missing, never with a guess."""

    model_config = ConfigDict( extra="forbid" )

    question: str
    found:    bool
    answer:   str = Field( max_length=1200 )
    evidence: list[ Evidence ] = Field( default_factory=list )
    missing:  list[ str ] = Field( default_factory=list )
    subject:  str | None = None                                       # the object or task it is about


class Health( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    ok:             bool
    build:          str
    schema_version: int
    objects:        int
    events:         int
    max_seq:        int
    last_event_at:  float | None
    now:            float
    store:          str
    sssf:           str                                               # "readable", "absent", or the error

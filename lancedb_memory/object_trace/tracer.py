"""ObjectTracer: how a component records itself and what happens to it.

    tracer = default_tracer()                       # composition root only
    with tracer.begin( "bridge.task_run", f"bridge.task_run:{ task_id }:{ attempt }",
                       interface="ITaskGatewayClient", implementation="HttpTaskGateway",
                       task_id=task_id, attempt=attempt, heartbeat_seconds=30 ) as trace:
        trace.heartbeat()
        trace.progress( "agent started" )
        trace.succeed({ "summary": "..." })

Correlation is ambient: `trace_scope( run_id=..., task_id=..., adw_id=... )`
sets what every object begun inside it inherits, and a `with trace:` block
makes that trace the parent of whatever is begun inside it.

Telemetry never breaks the workflow. `SafeSink` swallows a failed write, says
so on stderr the first time, counts the rest, and the next write that succeeds
records a `telemetry` event saying how many were lost -- so a gap in the trace
is visible in the trace itself rather than being silence.
"""
from __future__ import annotations

import contextlib
import itertools
import os
import re
import socket
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from .interfaces import IRedactor, ITraceClock, ITraceSink
from .models import TERMINAL, EventKind, State, TraceEvent, TraceObject
from .redact import BoundedRedactor

T = TypeVar( "T" )

PROCESS_SESSION = uuid.uuid4().hex[ :16 ]
_EVENT_NUMBERS  = itertools.count( 1 )        # one sequence per process, shared by every tracer in it, so ids never collide


class Scope( BaseModel ):
    model_config = ConfigDict( frozen=True )

    run_id:    str | None = None
    task_id:   str | None = None
    adw_id:    str | None = None
    parent_id: str | None = None


_scope: ContextVar[ Scope ] = ContextVar( "object_trace_scope", default=Scope())


def current_scope() -> Scope:
    return _scope.get()


@contextlib.contextmanager
def trace_scope( **fields: str | None ) -> Iterator[ Scope ]:
    """Everything begun inside inherits these fields. A None leaves the outer value in place."""
    merged = _scope.get().model_copy( update={ k: v for k, v in fields.items() if v is not None })
    token  = _scope.set( merged )
    try:
        yield merged
    finally:
        _scope.reset( token )


class SystemClock:
    def now( self ) -> float:
        return time.time()


class NullSink:
    def put_object( self, obj: TraceObject ) -> None:
        return None

    def put_event( self, event: TraceEvent ) -> bool:
        return True


class SafeSink:
    """An ITraceSink Decorator: a failed write is counted and reported, never raised."""

    def __init__( self, inner: ITraceSink, clock: ITraceClock | None = None, stream=None ) -> None:
        self._inner   = inner
        self._clock   = clock or SystemClock()
        self._stream  = stream if stream is not None else sys.stderr
        self._lock    = threading.Lock()
        self.failures = 0
        self.lost     = 0                   # failures not yet reported inside the trace
        self.last_error: str | None = None

    def put_object( self, obj: TraceObject ) -> None:
        self._guard( lambda: self._inner.put_object( obj ))

    def put_event( self, event: TraceEvent ) -> bool:
        return bool( self._guard( lambda: self._inner.put_event( event )))

    def _guard( self, write: Callable[[], object ]) -> object:
        try:
            out = write()
        except Exception as exc:              # noqa: BLE001 -- telemetry must not break the workflow
            with self._lock:
                self.failures += 1
                self.lost     += 1
                self.last_error = f"{ type( exc ).__name__ }: { str( exc )[ :200 ]}"
                first = self.failures == 1
            if first:
                print( f"object_trace: a telemetry write failed and was dropped ({ self.last_error }); "
                       "the workflow continues, later drops are counted", file=self._stream, flush=True )
            return None
        self._report_lost()
        return out

    def _report_lost( self ) -> None:
        with self._lock:
            lost, self.lost = self.lost, 0
        if not lost:
            return
        now = self._clock.now()
        oid = f"telemetry:{ PROCESS_SESSION }"
        try:
            self._inner.put_object( TraceObject( object_id=oid, kind="telemetry", implementation=type( self._inner ).__name__,
                                                 module=__name__, label="telemetry drops in this process", created_at=now,
                                                 host=socket.gethostname(), pid=os.getpid(), process_session=PROCESS_SESSION,
                                                 state="failed", error={ "dropped": lost, "last_error": self.last_error }))
            self._inner.put_event( TraceEvent( event_id=f"{ PROCESS_SESSION }.lost.{ uuid.uuid4().hex[ :8 ]}", object_id=oid,
                                               at=now, kind="telemetry", state="failed",
                                               message=f"{ lost } telemetry write(s) were dropped before this one" ))
        except Exception:                     # noqa: BLE001
            with self._lock:
                self.lost += lost


class ObjectTracer:
    def __init__( self, sink: ITraceSink, redactor: IRedactor | None = None, clock: ITraceClock | None = None,
                  host: str | None = None, pid: int | None = None, process_session: str = PROCESS_SESSION ) -> None:
        self.sink      = sink
        self.redactor  = redactor or BoundedRedactor()
        self.clock     = clock or SystemClock()
        self._host     = host or socket.gethostname()
        self._pid      = os.getpid() if pid is None else pid
        self._session  = process_session

    def next_event_id( self ) -> str:
        return f"{ self._session }.{ next( _EVENT_NUMBERS )}"

    def begin( self, kind: str, object_id: str | None = None, *, interface: str = "", implementation: str = "",
               module: str = "", label: str = "", inputs: object = None, deps: tuple[ str, ...] | list[ str ] = (),
               heartbeat_seconds: float | None = None, phase: str | None = None, attempt: int | None = None,
               run_id: str | None = None, task_id: str | None = None, adw_id: str | None = None,
               parent_id: str | None = None, state: State = "running" ) -> "Trace":
        scope = _scope.get()
        now   = self.clock.now()
        oid   = safe_id( object_id or f"{ kind }:{ uuid.uuid4().hex[ :12 ]}" )
        ids   = { k: safe_id( v ) if v else None for k, v in (( "run_id", run_id or scope.run_id ), ( "task_id", task_id or scope.task_id ),
                                                            ( "adw_id", adw_id or scope.adw_id ), ( "parent_id", parent_id or scope.parent_id ))}
        try:
            obj = TraceObject( object_id=oid, kind=kind, interface=interface, implementation=implementation, module=module,
                               label=self.redactor.text( label, 200 ), created_at=now, host=self._host, pid=self._pid,
                               process_session=self._session, **ids, phase=phase, attempt=attempt, span_id=oid,
                               deps=[ safe_id( d ) for d in list( deps )[ :20 ] if d ], heartbeat_seconds=heartbeat_seconds, state=state,
                               inputs=self.redactor.clean( inputs ), last_event_at=now, last_event_kind="created" )
        except Exception as exc:              # noqa: BLE001 -- a record that cannot be built is dropped, not raised
            print( f"object_trace: { kind } could not be recorded ({ type( exc ).__name__ }: { str( exc )[ :160 ]})",
                   file=sys.stderr, flush=True )
            return Trace( self, TraceObject( object_id=oid, kind="telemetry", created_at=now ), live=False )
        trace = Trace( self, obj )
        self.sink.put_object( obj )
        trace._emit( "created", state=state, message=label )
        return trace

    def construct( self, kind: str, factory: Callable[[], T ], *, interface: str = "", label: str = "",
                   inputs: object = None, object_id: str | None = None ) -> T:
        """Build something, recording it -- or recording exactly how its construction failed, then re-raising."""
        try:
            made = factory()
        except Exception as exc:
            trace = self.begin( kind, object_id, interface=interface, label=label or f"constructing { kind }",
                                inputs=inputs, state="construct_failed" )
            trace._finish( "construct_failed", "construct_failed", f"construction failed: { type( exc ).__name__ }",
                           error=_error( exc, self.redactor ))
            raise
        self.begin( kind, object_id, interface=interface, implementation=type( made ).__name__,
                    module=type( made ).__module__, label=label, inputs=inputs, state="created" )
        return made


class Trace:
    """One object's handle. Each method writes one event; none of them raises because of telemetry."""

    def __init__( self, tracer: ObjectTracer, obj: TraceObject, live: bool = True ) -> None:
        self._tracer = tracer
        self.object  = obj
        self._token  = None
        self._live   = live

    @property
    def object_id( self ) -> str:
        return self.object.object_id

    @property
    def done( self ) -> bool:
        return self.object.state in TERMINAL

    def __enter__( self ) -> "Trace":
        self._token = _scope.set( _scope.get().model_copy( update={ "parent_id": self.object_id }))
        return self

    def __exit__( self, exc_type, exc, tb ) -> bool:
        if self._token is not None:
            _scope.reset( self._token )
            self._token = None
        if exc is not None and not self.done:
            self.fail( exc )
        elif not self.done:
            self.succeed()
        return False

    # -- events -------------------------------------------------------------------

    def heartbeat( self, message: str = "" ) -> None:
        self._emit( "heartbeat", message=message )

    def progress( self, message: str, data: object = None ) -> None:
        self._emit( "progress", state="running" if self.object.state != "running" else None, message=message, data=data )

    def waiting( self, message: str, data: object = None ) -> None:
        self._emit( "waiting", state="waiting", message=message, data=data )

    def event( self, kind: EventKind, message: str = "", data: object = None, state: State | None = None ) -> None:
        self._emit( kind, state=state, message=message, data=data )

    def output( self, outputs: object ) -> None:
        if not self._live:
            return
        self.object = self.object.model_copy( update={ "outputs": self._tracer.redactor.clean( outputs )})
        self._tracer.sink.put_object( self.object )
        self._emit( "output", message="output recorded", data=outputs )

    def succeed( self, outputs: object = None, message: str = "" ) -> None:
        self._finish( "succeeded", "succeeded", message or "succeeded", outputs=outputs )

    def cancel( self, message: str = "cancelled" ) -> None:
        self._finish( "cancelled", "cancelled", message )

    def fail( self, error: BaseException | str, data: object = None, outputs: object = None ) -> None:
        detail = _error( error, self._tracer.redactor ) if isinstance( error, BaseException ) \
                 else { "message": self._tracer.redactor.text( str( error ), 500 )}
        if data is not None:
            detail[ "data" ] = self._tracer.redactor.clean( data )
        self._finish( "failed", "failed", detail[ "message" ], outputs=outputs, error=detail )

    # -- plumbing -------------------------------------------------------------------

    def _finish( self, kind: EventKind, state: State, message: str, outputs: object = None, error: dict | None = None ) -> None:
        if not self._live:
            self.object = self.object.model_copy( update={ "state": state })
            return
        update: dict = { "state": state }
        if outputs is not None:
            update[ "outputs" ] = self._tracer.redactor.clean( outputs )
        if error is not None:
            update[ "error" ] = error
        self.object = self.object.model_copy( update=update )
        self._tracer.sink.put_object( self.object )
        self._emit( kind, state=state, message=message, data=error )

    def _emit( self, kind: EventKind, state: State | None = None, message: str = "", data: object = None ) -> None:
        tracer = self._tracer
        if not self._live:
            return
        try:
            event = TraceEvent( event_id=tracer.next_event_id(), object_id=self.object_id, at=tracer.clock.now(), kind=kind,
                                state=state, message=tracer.redactor.text( message, 500 ),
                                data=tracer.redactor.clean( data ), run_id=self.object.run_id, task_id=self.object.task_id )
        except Exception as exc:              # noqa: BLE001 -- a bad value is a telemetry failure, not the caller's
            print( f"object_trace: an event could not be built ({ type( exc ).__name__ })", file=sys.stderr, flush=True )
            return
        if state is not None:
            self.object = self.object.model_copy( update={ "state": state })
        self.object = self.object.model_copy( update={ "last_event_at": event.at, "last_event_kind": kind })
        tracer.sink.put_event( event )


def safe_id( value: str ) -> str:
    """Any string as an id the store accepts: unsafe characters become "_", and it is at most 200 long."""
    cleaned = re.sub( r"[^A-Za-z0-9_.:@/-]", "_", str( value ))[ :200 ]
    return cleaned or "unnamed"


def _error( exc: BaseException, redactor: IRedactor ) -> dict:
    frames = traceback.extract_tb( exc.__traceback__ )[ -3: ] if exc.__traceback__ else []
    return { "type": type( exc ).__name__, "message": redactor.text( str( exc ), 500 ),
             "where": [ f"{ os.path.basename( f.filename )}:{ f.lineno } { f.name }" for f in frames ]}

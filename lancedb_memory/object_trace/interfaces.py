"""The interfaces object tracing is written against. Protocols only.

`ITraceSink` is the write side every instrumented process holds; `ITraceReader`
is the read side only the inspection service holds. Nothing outside
`adapters/` names SQLite.
"""
from __future__ import annotations

from typing import Protocol

from .models import TraceEvent, TraceObject


class ITraceSink( Protocol ):
    def put_object( self, obj: TraceObject ) -> None: ...                # insert, or update all but created_at

    def put_event( self, event: TraceEvent ) -> bool: ...                # False: that event_id was already stored


class ITraceReader( Protocol ):
    """Bounded, parameterized reads. Every list takes a limit and the store caps it."""

    def schema_version( self ) -> int: ...

    def counts( self ) -> tuple[ int, int, int, float | None ]: ...       # objects, events, max seq, last event at

    def object( self, object_id: str ) -> TraceObject | None: ...

    def objects( self, *, run_id: str | None = None, task_id: str | None = None, kind: str | None = None,
                 state: str | None = None, text: str | None = None, since: float | None = None,
                 limit: int = 100 ) -> list[ TraceObject ]: ...         # newest activity first

    def events( self, *, after_seq: int = 0, object_id: str | None = None, run_id: str | None = None,
                task_id: str | None = None, limit: int = 200 ) -> list[ TraceEvent ]: ...   # oldest first

    def latest_events( self, object_id: str, limit: int = 50 ) -> list[ TraceEvent ]: ...   # newest last

    def dependents( self, object_id: str ) -> list[ str ]: ...

    def runs( self, limit: int = 50 ) -> list[ dict ]: ...                # run_id, objects, states, first/last at


class IRedactor( Protocol ):
    def clean( self, value: object ) -> dict | None: ...                 # bounded, secrets masked, private text hashed

    def text( self, value: str, limit: int = 500 ) -> str: ...


class ITraceClock( Protocol ):
    def now( self ) -> float: ...                                       # epoch seconds

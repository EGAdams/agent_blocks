"""Read-only bridge requests over the same Inspector used by the debugger UI."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_bridge_worker.redaction import SecretScanner
from object_trace.adapters.sqlite_store import SqliteTraceStore
from object_trace.adapters.sssf_reader import SssfReader
from object_trace.factory import SSSF_DB
from object_trace.inspector import Inspector
from object_trace.interfaces import ITraceClock
from object_trace.service import source_build
from object_trace.tracer import SystemClock

from ..models import Envelope, TaskResult

TASK_ID = re.compile( r"(?<![\w-])t_[0-9a-f]{24}(?![\w-])" )
MAX_EVENTS = 200
MAX_OUTPUT_BYTES = 24000
MAX_REQUEST_CHARS = 8000


class ObjectDebuggerExecutor:
    """ITaskExecutor. The request selects a read; it never selects code or a path."""

    def __init__( self, db: Path, stale_seconds: float = 86400,
                  clock: ITraceClock | None = None ) -> None:
        self._db = Path( db )
        self._stale_seconds = stale_seconds
        self._clock = clock or SystemClock()
        self._scanner = SecretScanner()

    @property
    def performer( self ) -> str:
        return "object_trace:inspector"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        if envelope.sender not in { "chatgpt-bridge", "toyota-bridge" } or envelope.recipient != "object-debugger":
            return self._result( False, "Object Debugger only accepts requests from chatgpt-bridge or toyota-bridge." )
        if envelope.task_type != "inspect":
            return self._result( False, "Object Debugger only accepts inspect requests." )
        text = envelope.text.strip()
        if not text or len( text ) > MAX_REQUEST_CHARS:
            return self._result( False, "Provide 1 to 8000 characters: status, recent [1-200], or task <id>." )
        ids = TASK_ID.findall( text )
        if len( ids ) > 1:
            return self._result( False, "Provide one task id per request." )
        if not ids and re.search( r"\btask\b|\bt_", text, re.I ):
            return self._result( False, "Provide a task id matching t_ followed by exactly 24 lowercase hexadecimal characters." )
        request = "task" if ids else "recent" if re.search( r"\b(recent|events|changes|lifecycle)\b", text, re.I ) else "status"
        limit = 50
        if request == "recent":
            number = re.search( r"(?<!\w)([+-]?\d+)\s*$", text )
            if number:
                # Clamp before int conversion, including arbitrarily long input digits.
                digits = number[ 1 ].lstrip( "+-" ).lstrip( "0" ) or "0"
                limit = 1 if number[ 1 ].startswith( "-" ) else min( MAX_EVENTS, int( digits ) if len( digits ) <= 3 else MAX_EVENTS )
                limit = max( 1, limit )
        try:
            store = SqliteTraceStore( self._db, read_only=True )
            try:
                inspector = Inspector( store, self._clock, SssfReader( SSSF_DB ))
                if request == "task":
                    answer = inspector.ask( "where_stopped", subject=ids[ 0 ])
                    return self._result( answer.found, answer.answer, answer.model_dump())
                health = inspector.health( source_build(), str( self._db ))
                if request == "status":
                    age = max( 0.0, health.now - health.last_event_at ) if health.last_event_at is not None else None
                    freshness = "empty" if age is None else "stale" if age > self._stale_seconds else "fresh"
                    return self._result( health.ok,
                        f"Object trace available; { freshness }; { health.objects } objects, { health.events } events, latest sequence { health.max_seq }.",
                        { **health.model_dump(), "freshness": freshness, "age_seconds": age,
                          "stale_seconds": self._stale_seconds })
                after = max( 0, health.max_seq - limit )
                changes = inspector.changes( after, limit=limit )
                # Objects may carry large inputs/outputs; events alone answer this request.
                details = { k: v for k, v in changes.items() if k != "objects" }
                details.update({ "after_seq": after, "limit": limit })
                return self._result( True, f"Recent object lifecycle events (up to { limit }, latest sequence { health.max_seq }).", details )
            finally:
                store.close()
        except Exception:                       # failed work is a TaskResult; exception messages may contain secrets
            return self._result( False, "Object trace unavailable; its existing read-only source could not be inspected." )

    def _result( self, ok: bool, text: str, details: dict[ str, Any ] | None = None ) -> TaskResult:
        details = details or {}
        # Scan before trimming as well: a secret in omitted evidence still withholds the response.
        if self._scanner.found( text + "\n" + json.dumps( details, ensure_ascii=False )) or self._contains_secret( details ):
            return TaskResult( ok=False, text="Object Debugger response withheld because it contains a possible secret.",
                               performer=self.performer )
        def body() -> str:
            return text + "\n" + json.dumps( details, ensure_ascii=False )
        while len( body().encode( "utf-8" )) > MAX_OUTPUT_BYTES and details.get( "events" ):
            details[ "events" ].pop( 0 )          # retain the newest events within the byte budget
            details[ "truncated" ] = True
        if len( body().encode( "utf-8" )) > MAX_OUTPUT_BYTES:
            return TaskResult( ok=False, text="Object Debugger response withheld because it exceeds the output size limit.",
                               performer=self.performer )
        return TaskResult( ok=ok, text=body(), details=details, performer=self.performer )

    def _contains_secret( self, value: Any, key: str = "" ) -> bool:
        # JSON quotes around a field name can hide an assigned secret from a text scanner.
        # Present each leaf as an assignment, retaining the shared scanner's patterns.
        if isinstance( value, dict ):
            return any( self._contains_secret( v, str( k )) for k, v in value.items())
        if isinstance( value, list ):
            return any( self._contains_secret( v, key ) for v in value )
        return bool( self._scanner.found( f"{ key }={ value }" ))

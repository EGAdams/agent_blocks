"""default_tracer(): the one place a process gets its tracer. Composition roots call it; nothing else names a store.

`OBJECT_TRACE_DB` moves the file (default `adws/adw_data/object_trace.db` in
this checkout); `OBJECT_TRACE=off` turns tracing into a no-op. A store that
cannot be opened is itself a telemetry failure: it is reported once on stderr
and the process gets a no-op tracer, so a broken trace file never stops a
workflow from starting.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .tracer import NullSink, ObjectTracer, SafeSink

CHECKOUT   = Path( __file__ ).resolve().parents[ 2 ]
DEFAULT_DB = CHECKOUT / "adws" / "adw_data" / "object_trace.db"
SSSF_DB    = CHECKOUT / "adws" / "adw_data" / "sssf.db"

_shared: ObjectTracer | None = None


def trace_db_path() -> Path:
    return Path( os.environ.get( "OBJECT_TRACE_DB" ) or DEFAULT_DB )


def default_tracer() -> ObjectTracer:
    global _shared
    if _shared is not None:
        return _shared
    if os.environ.get( "OBJECT_TRACE", "" ).lower() in ( "off", "0", "false", "no" ):
        _shared = ObjectTracer( NullSink())
        return _shared
    try:
        from .adapters.sqlite_store import SqliteTraceStore
        sink = SafeSink( SqliteTraceStore( trace_db_path()))
    except Exception as exc:                  # noqa: BLE001 -- telemetry must not stop the process
        print( f"object_trace: the trace store could not be opened ({ type( exc ).__name__ }: { str( exc )[ :160 ]}); "
               "tracing is off for this process", file=sys.stderr, flush=True )
        sink = SafeSink( NullSink())
        sink.failures, sink.last_error = 1, f"{ type( exc ).__name__ }"
    _shared = ObjectTracer( sink )
    return _shared

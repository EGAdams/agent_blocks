"""IStructuredLog: one JSON object per line. Callers pass facts, never values a
person typed -- an event is logged by kind and session, not by content."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Protocol

from .models import utc_now


class IStructuredLog( Protocol ):
    def write( self, event: str, **fields: Any ) -> None: ...


class JsonLinesLog:
    def __init__( self, path: Path, echo: bool = False ) -> None:
        self._path = Path( path )
        self._path.parent.mkdir( parents=True, exist_ok=True )
        self._echo = echo

    def write( self, event: str, **fields: Any ) -> None:
        line = json.dumps({ "at": utc_now(), "event": event, **fields }, default=str )
        with self._path.open( "a", encoding="utf-8" ) as fh:
            fh.write( line + "\n" )
        if self._echo:
            print( line, file=sys.stderr, flush=True )


class NullLog:
    def write( self, event: str, **fields: Any ) -> None:
        pass

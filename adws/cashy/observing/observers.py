"""ICashyObserver implementations: a JSON-lines log, and the terminal."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import CashyEvent
from ..storage.sanitizer import mask_text


class JsonlCashyObserver:
    """One line per activity. Details are masked like everything stored."""

    def __init__( self, path: Path ) -> None:
        self._path = Path( path )

    def notify( self, session_id: str, event: CashyEvent ) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with self._path.open( "a", encoding="utf-8" ) as log:
            log.write( json.dumps({ "session": session_id, "activity": event.activity,
                                    "detail": mask_text( event.detail ), "at": event.at }) + "\n" )


class ConsoleCashyObserver:
    def __init__( self, write=print ) -> None:
        self._write = write

    def notify( self, session_id: str, event: CashyEvent ) -> None:
        self._write( f"  · { event.activity }{ ': ' + mask_text( event.detail ) if event.detail else '' }" )

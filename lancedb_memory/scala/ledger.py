"""IScalaLedger: one append-only JSONL line per scan Scala looked at.

`~/scala/ledger.jsonl` (`SCALA_HOME` overrides), folder 0700, file 0600 --
OCR'd bills and probate letters are personal, so the reasons stay terse and
the file stays private. Nothing is rewritten; it is also the history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Decision, FilingOutcome

#: Decisions that mean "this image is dealt with": seeing it again changes nothing.
SETTLED = frozenset({ Decision.FILED, Decision.INBOX, Decision.MAZDA })


def default_home() -> Path:
    return Path( os.environ.get( "SCALA_HOME", Path.home() / "scala" ))


class JsonlScalaLedger:
    def __init__( self, home: Path | None = None ) -> None:
        self._home = home or default_home()
        self._path = self._home / "ledger.jsonl"

    def _all( self ) -> list[ FilingOutcome ]:
        if not self._path.exists():
            return []
        outcomes: list[ FilingOutcome ] = []
        for line in self._path.read_text( encoding="utf-8" ).splitlines():
            if line.strip():
                try:
                    outcomes.append( FilingOutcome.model_validate_json( line ))
                except ValueError:
                    continue
        return outcomes

    def seen( self, sha256: str ) -> FilingOutcome | None:
        return next(( o for o in reversed( self._all()) if o.sha256 == sha256 and o.decision in SETTLED ), None )

    def record( self, outcome: FilingOutcome ) -> None:
        self._home.mkdir( parents=True, exist_ok=True )
        os.chmod( self._home, 0o700 )
        fd = os.open( self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600 )
        with os.fdopen( fd, "a", encoding="utf-8" ) as handle:
            handle.write( outcome.model_dump_json() + "\n" )
            handle.flush()
            os.fsync( handle.fileno())

    def recent( self, limit: int ) -> list[ FilingOutcome ]:
        return self._all()[ -limit: ]


class InMemoryScalaLedger:
    def __init__( self ) -> None:
        self.outcomes: list[ FilingOutcome ] = []

    def seen( self, sha256: str ) -> FilingOutcome | None:
        return next(( o for o in reversed( self.outcomes ) if o.sha256 == sha256 and o.decision in SETTLED ), None )

    def record( self, outcome: FilingOutcome ) -> None:
        self.outcomes.append( outcome )

    def recent( self, limit: int ) -> list[ FilingOutcome ]:
        return self.outcomes[ -limit: ]


def summary( outcome: FilingOutcome ) -> str:
    """The one sentence a person reads about a scan."""
    name = Path( outcome.source ).name
    how = f"{ outcome.method.value }" + ( f" { outcome.confidence:.2f}" if outcome.confidence is not None else "" )
    why = f": { outcome.reason.rstrip( '.' ) }" if outcome.reason else ""
    if outcome.decision is Decision.FILED:
        new = " (new folder)" if outcome.created_folder else ""
        return f"Scala filed { name } under { outcome.folder }{ new }, by { how }{ why }."
    if outcome.decision is Decision.INBOX:
        return f"Scala could not tell what { name } is, so it is waiting in scanned_documents/inbox ({ how }){ why }."
    if outcome.decision is Decision.MAZDA:
        return f"Scala left { name } to Mazda: it is finance paperwork ({ how }){ why }."
    if outcome.decision is Decision.DUPLICATE:
        return f"Scala already handled { name } (same image){ why }."
    return f"Scala could not file { name }{ why }."

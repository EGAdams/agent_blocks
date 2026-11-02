"""The roles Scala's desk is written against. Concretes are named only in a composition root."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import Classification, FilingOutcome


class IOcrReader( Protocol ):
    def read( self, image: Path ) -> str:
        """The page's text, or "" when it cannot be read. Never raises."""
        ...


class IMazdaVerdicts( Protocol ):
    def doc_kind( self, image: Path ) -> str | None:
        """What Mazda's intake classifier called this scan, or None if it has not said."""
        ...

    def left_for_scala( self, image: Path ) -> bool:
        """True when the dashboard skipped Mazda's turn on this scan as not financial."""
        ...


class IDocumentClassifier( Protocol ):
    def classify( self, text: str, image: Path, folders: tuple[ str, ... ]) -> Classification:
        """Never raises: an unreadable answer is UNSURE with the reason."""
        ...


class IFilingCabinet( Protocol ):
    def folders( self ) -> tuple[ str, ... ]: ...
    def is_allowed( self, folder: str ) -> bool: ...
    def file( self, image: Path, folder: str, sha256: str ) -> tuple[ Path, bool ]:
        """Copy the scan into `folder`; returns ( where it went, whether the folder was new )."""
        ...
    def inbox( self, image: Path, sha256: str ) -> Path: ...


class IScalaLedger( Protocol ):
    def seen( self, sha256: str ) -> FilingOutcome | None: ...
    def record( self, outcome: FilingOutcome ) -> None: ...
    def recent( self, limit: int ) -> list[ FilingOutcome ]: ...

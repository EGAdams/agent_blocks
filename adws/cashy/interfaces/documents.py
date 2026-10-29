from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import DownloadRecord


class IDownloadWatcher( Protocol ):
    """New, finished files in the download folder since `before`."""

    def before( self ) -> set[ str ]: ...

    def wait_new( self, before: set[ str ], timeout_seconds: float = 30.0 ) -> list[ Path ]: ...


class IDownloadVerifier( Protocol ):
    """Is this file what was asked for? Returns the reason it is not, or ""."""

    def problem( self, path: Path ) -> str: ...


class IDownloadLedger( Protocol ):
    def find( self, bank: str, account: str, period: str ) -> DownloadRecord | None: ...

    def add( self, record: DownloadRecord ) -> None: ...

    def recent( self, limit: int = 20 ) -> list[ DownloadRecord ]: ...

"""Watching the download folder and checking what lands in it.

Chrome may be on Windows, writing to "C:\\Users\\...\\BrowserRecorder";
Cashy runs in WSL and reads the same folder as /mnt/c/Users/.../BrowserRecorder.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path

PARTIAL = ( ".crdownload", ".tmp", ".part" )
_DRIVE  = re.compile( r"^([A-Za-z]):[\\/](.*)$" )


def local_path( directory: str ) -> Path:
    """A Windows path as this machine sees it; anything else unchanged."""
    match = _DRIVE.match( directory )
    if match:
        return Path( "/mnt" ) / match.group( 1 ).lower() / match.group( 2 ).replace( "\\", "/" )
    return Path( directory )


class FolderDownloadWatcher:
    def __init__( self, directory: Callable[[], str ], poll_seconds: float = 0.25 ) -> None:
        self._directory = directory
        self._poll      = poll_seconds

    def before( self ) -> set[ str ]:
        folder = local_path( self._directory())
        return { p.name for p in folder.iterdir()} if folder.is_dir() else set()

    def wait_new( self, before: set[ str ], timeout_seconds: float = 30.0 ) -> list[ Path ]:
        folder   = local_path( self._directory())
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if folder.is_dir():
                fresh   = [ p for p in folder.iterdir() if p.name not in before ]
                partial = [ p for p in fresh if p.name.endswith( PARTIAL )]
                done    = [ p for p in fresh if not p.name.endswith( PARTIAL ) and p.is_file()]
                if done and not partial:
                    return sorted( done )
            time.sleep( self._poll )
        return []


class FileDownloadVerifier:
    """Not empty, and a .pdf really is one."""

    def problem( self, path: Path ) -> str:
        if not path.is_file():
            return f"{ path.name } is not there"
        if path.stat().st_size == 0:
            return f"{ path.name } is empty"
        if path.suffix.lower() == ".pdf":
            with path.open( "rb" ) as handle:
                if not handle.read( 5 ).startswith( b"%PDF" ):
                    return f"{ path.name } says it is a PDF but is not one"
        return ""


def sha256( path: Path ) -> str:
    digest = hashlib.sha256()
    with path.open( "rb" ) as handle:
        for block in iter( lambda: handle.read( 65536 ), b"" ):
            digest.update( block )
    return digest.hexdigest()

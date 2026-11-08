"""Watching the download folder and checking what lands in it.

Chrome may be on Windows, writing to "C:\\Users\\...\\BrowserRecorder"; a workflow in WSL
reads that same folder as /mnt/c/Users/.../BrowserRecorder. Cashy's statements and Browser
Control's browser.download both wait here, so neither reports a file that never arrived.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path

PARTIAL = ( ".crdownload", ".tmp", ".part" )
_DRIVE  = re.compile( r"^([A-Za-z]):[\\/](.*)$" )


def stamp( path: Path ) -> str:
    """One file's identity *and* state: name, size, and when it was last written.

    Names alone are not enough. Chrome, given a download folder over DevTools, **overwrites** a file
    of the same name instead of adding "(1)" -- so asking for the same statement or the same .tif twice
    left the folder looking untouched, and the workflow honestly reported that nothing had arrived. A
    rewritten file has a new mtime, so a stamp sees it.
    """
    try:
        info = path.stat()
    except OSError:
        return path.name
    return f"{ path.name }\x00{ info.st_size }\x00{ info.st_mtime_ns }"


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
        """A stamp per file, not a name per file -- see `stamp` for the overwrite this catches."""
        folder = local_path( self._directory())
        return { stamp( p ) for p in folder.iterdir()} if folder.is_dir() else set()

    def wait_new( self, before: set[ str ], timeout_seconds: float = 30.0 ) -> list[ Path ]:
        """The finished files that are not in `before`, or [] if none arrived in time.

        A file is finished when nothing beside it is still partial *and* its own stamp stopped moving
        for one poll. The second half matters because an overwrite is written in place under the final
        name: for an instant the file is short, and without the settle check a truncated statement
        would be handed back as the download.
        """
        folder   = local_path( self._directory())
        deadline = time.monotonic() + timeout_seconds
        settled: set[ str ] = set()
        while time.monotonic() < deadline:
            if folder.is_dir():
                fresh   = [ p for p in folder.iterdir() if stamp( p ) not in before ]
                partial = [ p for p in fresh if p.name.endswith( PARTIAL )]
                done    = [ p for p in fresh if not p.name.endswith( PARTIAL ) and p.is_file()]
                now     = { stamp( p ) for p in done }
                if done and not partial and now and now <= settled:
                    return sorted( done )
                settled = now
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

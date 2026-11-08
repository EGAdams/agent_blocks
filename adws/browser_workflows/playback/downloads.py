"""Watching the download folder and checking what lands in it.

Chrome may be on Windows, writing to "C:\\Users\\...\\BrowserRecorder"; a workflow in WSL
reads that same folder as /mnt/c/Users/.../BrowserRecorder. Cashy's statements and Browser
Control's browser.download both wait here, so neither reports a file that never arrived.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import time
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel

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
    """Not empty, a .pdf really is one, and a .tif (or any document name) is not a web page in disguise.

    The last check is the one a slow government site needs: when a session times out, the link to a
    .tif answers with the sign-in page, and Chrome saves that HTML under the .tif name."""

    def problem( self, path: Path ) -> str:
        if not path.is_file():
            return f"{ path.name } is not there"
        if path.stat().st_size == 0:
            return f"{ path.name } is empty"
        if path.suffix.lower() == ".pdf":
            with path.open( "rb" ) as handle:
                if not handle.read( 5 ).startswith( b"%PDF" ):
                    return f"{ path.name } says it is a PDF but is not one"
        from file_inspection.sniff import mismatch, sniff_mime
        return mismatch( path, sniff_mime( path ))


# -- filing: a fresh folder per download, then a name that never overwrites ---------------------

INCOMING = ".incoming"
_UNSAFE   = re.compile( r'[<>:"/\\|?*\x00-\x1f]' )
_RESERVED = { "CON", "PRN", "AUX", "NUL", *( f"COM{ n }" for n in range( 1, 10 )), *( f"LPT{ n }" for n in range( 1, 10 ))}
MAX_NAME  = 150


class KeptFile( BaseModel ):
    """One finished download, moved out of its staging folder under a name nothing else has."""
    original_name: str          # what Chrome saved it as (the site's suggested name)
    name: str                   # what it is called now
    path: str                   # as this machine sees it
    chrome_path: str            # as Chrome's machine sees it (a Windows path from WSL)
    renamed: bool               # True when a file of the original name was already there


def safe_name( raw: str ) -> str:
    """A bare file name that cannot climb out of a folder or collide with a Windows device name."""
    name = _UNSAFE.sub( "_", PureWindowsPath( raw.replace( "/", "\\" )).name ).strip().strip( "." ).strip()
    if not name:
        name = "download"
    stem, dot, suffix = name.rpartition( "." )
    if not dot:
        stem, suffix = name, ""
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    suffix = suffix[ :16 ]
    stem   = stem[ :MAX_NAME - len( suffix ) - 1 ]
    return f"{ stem }.{ suffix }" if suffix else stem


def unique_path( folder: Path, name: str ) -> Path:
    """`name`, or `stem (2).ext`, `stem (3).ext` ... -- the first that is free. Never an existing file."""
    candidate = folder / name
    stem, suffix = candidate.stem, candidate.suffix
    number = 2
    while candidate.exists():
        candidate = folder / f"{ stem } ({ number }){ suffix }"
        number += 1
    return candidate


def chrome_join( directory: str, *parts: str ) -> str:
    """Join on Chrome's machine: backslashes for a Windows folder, slashes otherwise."""
    if _DRIVE.match( directory ):
        return str( PureWindowsPath( directory, *parts ))
    return str( Path( directory, *parts ))


class StagedDownload:
    """One empty folder that only this download writes into, so whatever lands there is its file.

    Chrome **overwrites** a same-named file in its download folder. Staging means it can only ever
    overwrite inside this empty folder; `keep` then moves the file into the real folder under a name
    that is free, so an earlier download of the same document is never destroyed."""

    def __init__( self, final_dir: str, chrome_dir: str, poll_seconds: float = 0.25 ) -> None:
        self._final, self.chrome_dir = final_dir, chrome_dir
        self._local = local_path( chrome_dir )
        self._watcher = FolderDownloadWatcher( lambda: chrome_dir, poll_seconds )

    def before( self ) -> set[ str ]:
        return self._watcher.before()

    def wait_new( self, before: set[ str ], timeout_seconds: float = 30.0 ) -> list[ Path ]:
        return self._watcher.wait_new( before, timeout_seconds )

    def keep( self, files: list[ Path ]) -> list[ KeptFile ]:
        final_local = local_path( self._final )
        kept: list[ KeptFile ] = []
        for staged in files:
            wanted = safe_name( staged.name )
            target = unique_path( final_local, wanted )
            if self._local.resolve() not in staged.resolve().parents:
                raise ValueError( f"{ staged } is not inside this download's staging folder" )
            shutil.move( str( staged ), str( target ))
            kept.append( KeptFile( original_name=staged.name, name=target.name, path=str( target ),
                                   chrome_path=chrome_join( self._final, target.name ), renamed=target.name != wanted ))
        return kept

    def finish( self ) -> list[ str ]:
        """Remove the staging folder if it is empty; otherwise leave it and name what is still in it
        (a download still being written after the wait ran out is evidence, not litter)."""
        try:
            left = sorted( p.name for p in self._local.iterdir())
        except OSError:
            return []
        if not left:
            try:
                self._local.rmdir()
            except OSError:
                pass
        return left


class DownloadFiler:
    """Makes a StagedDownload under `<download folder>/.incoming/<random>`."""

    def __init__( self, poll_seconds: float = 0.25 ) -> None:
        self._poll = poll_seconds

    def stage( self, directory: str ) -> StagedDownload:
        chrome_dir = chrome_join( directory, INCOMING, secrets.token_hex( 6 ))
        local_path( chrome_dir ).mkdir( parents=True, exist_ok=False )
        return StagedDownload( directory, chrome_dir, self._poll )


def sha256( path: Path ) -> str:
    digest = hashlib.sha256()
    with path.open( "rb" ) as handle:
        for block in iter( lambda: handle.read( 65536 ), b"" ):
            digest.update( block )
    return digest.hexdigest()

"""INotesStore and INotesEndpoint for the dashboard's Notes, on this machine.

`FilesystemNotesStore` keeps the dashboard's own rules for `NOTES_DIR`
(`voice/note_repository.py` in the letta-code dashboard): a note is one
`*.md` file, its revision is the sha256 of its bytes, text ends with a
newline, a new note is `<YYYY-MM-DD>_<stem>[-N].md`. They are restated here
rather than imported because the dashboard is a separate checkout; a test
pins the revision rule so the two cannot drift apart unnoticed.

`HttpNotesEndpoint` reads `GET /api/notes` on loopback only -- the endpoint
the Notes page polls -- so "the page can see it" is checked against what the
browser is served, not against the disk.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..notes_agent import NoteChanged

LOOPBACK = re.compile( r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?/" )
MAX_READ = 2 * 1024 * 1024


def revision_of( raw: bytes ) -> str:
    return hashlib.sha256( raw ).hexdigest()


class FilesystemNotesStore:
    def __init__( self, directory: Path, today: Callable[[], datetime.date ] = datetime.date.today ) -> None:
        self._dir   = Path( directory )
        self._today = today

    def __repr__( self ) -> str:
        return f"FilesystemNotesStore({ str( self._dir )!r })"

    def names( self ) -> list[ str ]:
        if not self._dir.is_dir():
            return []
        return sorted(( p.name for p in self._dir.iterdir() if p.name.endswith( ".md" ) and p.is_file()), reverse=True )

    def read( self, filename: str ) -> tuple[ str, str ] | None:
        path = self._path( filename )
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        return raw.decode( "utf-8", errors="replace" ), revision_of( raw )

    def create( self, stem: str, text: str ) -> tuple[ str, str ]:
        self._dir.mkdir( parents=True, exist_ok=True )
        prefix, counter = self._today().strftime( "%Y-%m-%d" ), 1
        while True:
            name = f"{ prefix }_{ stem }{ '' if counter == 1 else f'-{ counter }' }.md"
            try:
                fd = os.open( self._path( name ), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644 )
            except FileExistsError:
                counter += 1
                continue
            raw = _terminated( text )
            with os.fdopen( fd, "wb" ) as handle:
                handle.write( raw )
            return name, revision_of( raw )

    def write( self, filename: str, text: str, expected_revision: str ) -> str:
        path    = self._path( filename )
        current = self.read( filename )
        if current is None or current[ 1 ] != expected_revision:
            raise NoteChanged( f"{ filename } is not at the revision that was read" )
        raw = _terminated( text )
        tmp = path.with_name( f".{ path.name }.tmp" )
        tmp.write_bytes( raw )
        os.replace( tmp, path )                 # the poller sees the old note or the new one, never half of one
        return revision_of( raw )

    def delete( self, filename: str, expected_revision: str ) -> None:
        current = self.read( filename )
        if current is None:
            return
        if current[ 1 ] != expected_revision:
            raise NoteChanged( f"{ filename } is not at the revision that was read" )
        self._path( filename ).unlink()

    def _path( self, filename: str ) -> Path:
        if os.path.basename( filename ) != filename or not filename.endswith( ".md" ) or filename.startswith( "." ):
            raise ValueError( "invalid note filename" )
        return self._dir / filename


class HttpNotesEndpoint:
    def __init__( self, base_url: str, timeout_seconds: float = 10.0 ) -> None:
        if not LOOPBACK.match( base_url.rstrip( "/" ) + "/" ):
            raise ValueError( "the Notes endpoint is read on loopback only" )
        self._base    = base_url.rstrip( "/" )
        self._timeout = timeout_seconds

    def __repr__( self ) -> str:
        return f"HttpNotesEndpoint({ self._base!r })"

    def served( self, filename: str ) -> tuple[ str, str ] | None:
        try:
            answer = self._get( "/api/notes?" + urllib.parse.urlencode({ "filename": filename }))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise OSError( f"the Notes endpoint answered { exc.code }" ) from None
        note = answer.get( "note" )
        return ( note, str( answer.get( "revision", "" ))) if isinstance( note, str ) else None

    def listed( self ) -> list[ str ]:
        notes = self._get( "/api/notes" ).get( "notes", [])
        return [ str( n ) for n in notes ] if isinstance( notes, list ) else []

    def _get( self, path: str ) -> dict:
        request = urllib.request.Request( self._base + path, headers={ "Cache-Control": "no-store" })
        try:
            with urllib.request.urlopen( request, timeout=self._timeout ) as response:
                out = json.loads( response.read( MAX_READ ) or b"{}" )
        except urllib.error.URLError as exc:
            if isinstance( exc, urllib.error.HTTPError ):
                raise
            raise OSError( f"the Notes endpoint is unreachable ({ exc.reason })" ) from None
        return out if isinstance( out, dict ) else {}


def _terminated( text: str ) -> bytes:
    return ( text if text.endswith( "\n" ) else f"{ text }\n" ).encode( "utf-8" )

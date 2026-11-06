"""INotesBridge over the dashboard's token-protected Notes interface, with urllib.

`/api/notes-bridge/v1` (`letta-code/dashboard/notes_bridge.py`) answers only
loopback callers holding a `notes:read` / `notes:write` token from
`~/.config/scan_bridge/clients.json`, and only for notes on its approved list.
This client holds the `agent-bridge-notes` token (read from a 0600 file by
`load_scan_token`); the token goes in the Authorization header and is never
logged or shown. The base URL is refused unless it is loopback, so the token
can never leave this machine. Each call sends `X-Bridge-Task` so the
dashboard's activity log groups its events under the Bridge task.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ..notes_agent import NoteChanged, NotesDenied

LOOPBACK = re.compile( r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?/" )
TASK     = re.compile( r"^t_[0-9a-f]{8,64}$" )
PREFIX   = "/api/notes-bridge/v1"
MAX_READ = 2 * 1024 * 1024


class HttpNotesBridge:
    def __init__( self, base_url: str, token: str, timeout_seconds: float = 15.0 ) -> None:
        if not LOOPBACK.match( base_url.rstrip( "/" ) + "/" ):
            raise ValueError( "the Notes interface is reached on loopback only" )
        self._base    = base_url.rstrip( "/" )
        self._token   = token
        self._timeout = timeout_seconds

    def __repr__( self ) -> str:            # never show the token
        return f"HttpNotesBridge({ self._base!r })"

    # -- INotesBridge ---------------------------------------------------------

    def list( self, task_id: str ) -> list[ str ]:
        notes = self._json( "GET", "/notes", task_id ).get( "notes", [])
        return [ str( n ) for n in notes ] if isinstance( notes, list ) else []

    def read( self, filename: str, task_id: str ) -> tuple[ str, str ]:
        answer = self._json( "GET", f"/notes/{ _quoted( filename )}", task_id )
        note, revision = answer.get( "note" ), answer.get( "revision" )
        if not isinstance( note, str ) or not isinstance( revision, str ):
            raise OSError( "the Notes interface answered without a note and revision" )
        return note, revision

    def write( self, filename: str, text: str, expected_revision: str, task_id: str ) -> str:
        body = json.dumps({ "filename": filename, "note": text, "expected_revision": expected_revision }).encode( "utf-8" )
        revision = self._json( "POST", f"/notes/{ _quoted( filename )}", task_id, body ).get( "revision" )
        if not isinstance( revision, str ) or not revision:
            raise OSError( "the Notes interface did not report the saved revision" )
        return revision

    # -- HTTP -------------------------------------------------------------------

    def _json( self, method: str, path: str, task_id: str, body: bytes | None = None ) -> dict:
        headers = { "Authorization": f"Bearer { self._token }", "Cache-Control": "no-store" }
        if TASK.match( task_id or "" ):
            headers[ "X-Bridge-Task" ] = task_id
        if body is not None:
            headers[ "Content-Type" ] = "application/json"
        request = urllib.request.Request( self._base + PREFIX + path, data=body, method=method, headers=headers )
        try:
            with urllib.request.urlopen( request, timeout=self._timeout ) as response:
                return _dict( response.read( MAX_READ ))
        except urllib.error.HTTPError as exc:
            answer = _dict( exc.read( 64 * 1024 ))
            code   = str( answer.get( "error", "" ))[ :60 ] or "error"
            if exc.code == 409:
                raise NoteChanged( f"it is now at { str( answer.get( 'revision', '?' ))[ :12 ]}" ) from None
            if exc.code == 404:
                raise FileNotFoundError( path ) from None
            if exc.code in ( 401, 403 ):
                raise NotesDenied( code, exc.code ) from None
            raise OSError( f"the Notes interface answered { exc.code } { code }" ) from None
        except urllib.error.URLError as exc:
            raise OSError( f"the Notes interface is unreachable ({ exc.reason })" ) from None


def _quoted( filename: str ) -> str:
    return urllib.parse.quote( filename, safe="" )


def _dict( raw: bytes ) -> dict:
    try:
        out = json.loads( raw or b"{}" )
    except ValueError:
        return {}
    return out if isinstance( out, dict ) else {}

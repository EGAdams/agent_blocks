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

from ..notes_ops.base import NoteChanged, NotesDenied
from ..notes_ops.names import original_of

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

    # -- INotesCatalog ---------------------------------------------------------

    def entries( self, query: str, category: int | None, task_id: str ) -> list[ dict ]:
        params = { k: v for k, v in { "q": query, "category": category }.items() if v not in ( "", None ) }
        params.setdefault( "detail", "1" )
        found = self._json( "GET", "/notes?" + urllib.parse.urlencode( params ), task_id ).get( "entries", [])
        return [ e for e in found if isinstance( e, dict )] if isinstance( found, list ) else []

    def highlights( self, filename: str, task_id: str ) -> dict:
        return self._json( "GET", f"/notes/{ _quoted( filename )}/highlights", task_id )

    def trash_list( self, task_id: str ) -> list[ dict ]:
        names = self._json( "GET", "/trash", task_id ).get( "trash", [])
        return [{ "trash_id": n, "filename": original_of( n )} for n in names if isinstance( n, str )] if isinstance( names, list ) else []

    # -- INotesLifecycle (the dashboard's create / rename / delete / restore / purge) --------

    def create( self, hint: str, text: str, task_id: str ) -> dict:
        answer = self._post( "/notes", { "note": text, "filename": hint }, task_id )
        return { "filename": answer.get( "filename" ), "revision": answer.get( "revision" )}

    def rename( self, filename: str, expected_revision: str, new_filename: str, task_id: str ) -> dict:
        answer = self._post( f"/notes/{ _quoted( filename )}/rename",
                             { "new_filename": new_filename, "expected_revision": expected_revision }, task_id )
        return { "filename": answer.get( "filename" ), "revision": answer.get( "revision" )}

    def trash( self, filename: str, expected_revision: str, task_id: str ) -> dict:
        answer = self._post( f"/notes/{ _quoted( filename )}/delete", { "expected_revision": expected_revision }, task_id )
        return { "trash_id": answer.get( "trashed_as" ), "filename": filename, "revision": expected_revision }

    def restore( self, trash_id: str, task_id: str ) -> dict:
        answer = self._post( f"/trash/{ _quoted( trash_id )}/restore", {}, task_id )
        return { "filename": answer.get( "restored_as" ), "revision": answer.get( "revision" )}

    def purge( self, trash_id: str, authorization: str, task_id: str ) -> dict:
        self._post( f"/trash/{ _quoted( trash_id )}/purge", { "authorization": authorization }, task_id )
        return { "trash_id": trash_id, "purged": True }

    # -- PDF attachments (the dashboard's notes_attachments.py; the note's text is never touched) ----------

    def attach( self, filename: str, pdf_base64: str, title: str, name: str, sha256: str, task_id: str ) -> dict:
        answer = self._post( f"/notes/{ _quoted( filename )}/attachments",
                             { "pdf_base64": pdf_base64, "title": title, "name": name, "sha256": sha256 }, task_id )
        if not isinstance( answer.get( "attachment" ), dict ):
            raise OSError( "the Notes interface did not report the attachment" )
        return answer

    def attachments( self, filename: str, task_id: str ) -> list[ dict ]:
        found = self._json( "GET", f"/notes/{ _quoted( filename )}/attachments", task_id ).get( "attachments", [])
        return [ a for a in found if isinstance( a, dict )] if isinstance( found, list ) else []

    def attachment( self, filename: str, attachment_id: str, task_id: str ) -> dict:
        info = self._json( "GET", f"/notes/{ _quoted( filename )}/attachments/{ _quoted( attachment_id )}",
                           task_id ).get( "attachment" )
        if not isinstance( info, dict ):
            raise OSError( "the Notes interface answered without the attachment" )
        return info

    def detach( self, filename: str, attachment_id: str, task_id: str ) -> dict:
        answer = self._post( f"/notes/{ _quoted( filename )}/attachments/{ _quoted( attachment_id )}/remove", {}, task_id )
        return answer.get( "attachment" ) if isinstance( answer.get( "attachment" ), dict ) else { "id": attachment_id }

    def _post( self, path: str, payload: dict, task_id: str ) -> dict:
        answer = self._json( "POST", path, task_id, json.dumps( payload ).encode( "utf-8" ))
        if not answer.get( "ok" ):
            raise OSError( "the Notes interface did not confirm the change" )
        return answer

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
            reason = str( answer.get( "message", "" ))[ :200 ]
            if exc.code == 409 and code == "name_exists":
                raise OSError( f"that name is already taken ({ reason or code })" ) from None
            if exc.code == 409:
                raise NoteChanged( f"it is now at { str( answer.get( 'revision', '?' ))[ :12 ]}" ) from None
            if exc.code == 404:
                raise FileNotFoundError( path ) from None
            if exc.code in ( 401, 403 ):
                raise NotesDenied( code + ( f": { reason }" if reason and exc.code == 403 else "" ), exc.code ) from None
            raise OSError( f"the Notes interface answered { exc.code } { code }" + ( f": { reason }" if reason else "" )) from None
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

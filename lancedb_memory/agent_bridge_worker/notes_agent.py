"""NotesAgent: the IAgentAdapter that reads and updates dashboard Notes for ChatGPT. No model in the path.

On Nov 4 Bridge agents reported Notes changes that never reached the page:
codex and codex-writer are jailed to the `letta_code` checkout, and the
dashboard's Notes page reads Markdown from `NOTES_DIR` (`~/notes`), which no
Bridge workspace contains. This agent is the one Bridge path that reaches that
store, and it reaches only its own diagnostic note:

    inspect     read_only        {"notes": "status", "marker": "NOTESDIAG-..."?}
    implement   workspace_write  {"notes": "marker_write", "marker": "NOTESDIAG-..."}
    implement   workspace_write  {"notes": "marker_clear"}

ChatGPT reads and updates *approved* notes by ID (the note's filename) through
the dashboard's token-protected Notes interface (`/api/notes-bridge/v1`,
`letta-code/dashboard/notes_bridge.py`), which enforces the token's scope and
the approved-notes list on the server, whatever this agent does:

    inspect     read_only        {"notes": "list"}
    inspect     read_only        {"notes": "read", "id": "<filename>"}
    implement   workspace_write  {"notes": "update", "id": "<filename>", "expected_revision": "<sha256>",
                                  "edits": [{"find": "<exact text>", "replace": "<text>"}, ...]}
                                 ... or "text": "<the whole note>" in place of "edits"

An update never guesses: the revision must be the one ChatGPT read, and each
`find` must occur exactly once in the note as it stands, or nothing is
written. Text outside the edits is carried over byte for byte. After the write
the note is read back twice -- through the interface and through the browser's
own `GET /api/notes` -- and the task succeeds only if both return the expected
text at the revision the write reported. Every step lands in the Notes
activity log, which the Worker Activity page shows.

The prompt is JSON written by code (Toyota's NotesBuilder, or ChatGPT through
the same contract); free text is refused. Every write is read-before-write
with the revision, then read back twice: from the store, and from the
dashboard's own read endpoint (`GET /api/notes?filename=`), the one the
browser polls. A note is this agent's only if its name is
`<date>_bridge-notes-diagnostic[-N].md` *and* it begins with HEADER -- the
deed draft and every other note fail both tests, so nothing here can change
or delete them.

The diagnostic operations return no note text: file names, revisions, byte
counts and whether the marker was seen at each boundary, nothing else. A
`read` returns the approved note's text, which is what it is for.
"""
from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import Signal

PERFORMER = "notes"
HEADER    = "# Bridge Notes diagnostic"
STEM      = "bridge-notes-diagnostic"
OWNED     = re.compile( rf"^\d{{4}}-\d{{2}}-\d{{2}}_{ STEM }(-\d+)?\.md$" )
MARKER    = r"^NOTESDIAG-[0-9a-f]{12}$"
OPERATION = { "status": "inspect", "marker_write": "implement", "marker_clear": "implement",
              "list": "inspect", "read": "inspect", "update": "implement" }
REVISION  = r"^[0-9a-f]{64}$"
NOTE_ID   = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md$"
MAX_EDITS = 50


class NoteChanged( Exception ):
    """The note's revision was not the one read just before the write."""


class NotesDenied( Exception ):
    """The Notes interface refused the call: no token, a bad token, a missing scope, or a note not approved."""

    def __init__( self, code: str, status: int ) -> None:
        super().__init__( f"{ code } ({ status })" )
        self.code, self.status = code, status


class INotesBridge( Protocol ):
    """The dashboard's authorized Notes interface (`/api/notes-bridge/v1`)."""

    def list( self, task_id: str ) -> list[ str ]: ...                                   # approved notes only

    def read( self, filename: str, task_id: str ) -> tuple[ str, str ]: ...              # ( text, revision )

    def write( self, filename: str, text: str, expected_revision: str, task_id: str ) -> str: ...   # new revision


class INotesActivity( Protocol ):
    """Where this agent records what it did, for the Worker Activity page. Never note text."""

    def record( self, event: str, **fields: str ) -> None: ...


class INotesStore( Protocol ):
    """The Markdown store the dashboard's Notes page reads (NOTES_DIR)."""

    def names( self ) -> list[ str ]: ...                                 # every *.md, newest first

    def read( self, filename: str ) -> tuple[ str, str ] | None: ...      # ( text, revision ), None if absent

    def create( self, stem: str, text: str ) -> tuple[ str, str ]: ...    # a new dated, unique file: ( filename, revision )

    def write( self, filename: str, text: str, expected_revision: str ) -> str: ...   # NoteChanged on a mismatch

    def delete( self, filename: str, expected_revision: str ) -> None: ...          # NoteChanged on a mismatch


class INotesEndpoint( Protocol ):
    """The dashboard's read endpoint, exactly as the browser sees it."""

    def served( self, filename: str ) -> tuple[ str, str ] | None: ...    # ( text, revision ), None on a 404

    def listed( self ) -> list[ str ]: ...


class NoteEdit( BaseModel ):
    """Replace one exact, unique occurrence of `find` with `replace` (empty `replace` deletes it)."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    find:    str = Field( min_length=1 )
    replace: str = ""


class NotesRequest( BaseModel ):
    """The one prompt shape this agent accepts."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    notes:             Literal[ "status", "marker_write", "marker_clear", "list", "read", "update" ]
    marker:            str | None = Field( default=None, pattern=MARKER )
    id:                str | None = Field( default=None, pattern=NOTE_ID )
    expected_revision: str | None = Field( default=None, pattern=REVISION )
    edits:             list[ NoteEdit ] | None = Field( default=None, min_length=1, max_length=MAX_EDITS )
    text:              str | None = None

    def problem( self ) -> str | None:
        """Why this combination of fields is not a request, or None."""
        approved_fields = ( self.id, self.expected_revision, self.edits, self.text )
        if self.notes in ( "status", "marker_write", "marker_clear", "list" ):
            if any( f is not None for f in approved_fields ):
                return f"{ self.notes } takes no id, revision, edits or text"
            if self.notes == "marker_write" and self.marker is None:
                return "marker_write needs a marker"
            return None
        if self.marker is not None:
            return f"{ self.notes } takes no marker"
        if self.id is None:
            return f"{ self.notes } needs the note's id (its filename, from list)"
        if self.notes == "read":
            return None if all( f is None for f in approved_fields[ 1: ]) else "read takes only an id"
        if self.expected_revision is None:
            return "update needs expected_revision: the revision a read of this note returned"
        if ( self.edits is None ) == ( self.text is None ):
            return "update takes exactly one of edits or text"
        if self.text is not None and not self.text.strip():
            return "update text must not be blank"
        return None


class NotesAgent:
    def __init__( self, store: INotesStore, endpoint: INotesEndpoint, bridge: INotesBridge | None = None,
                  activity: INotesActivity | None = None ) -> None:
        self._store    = store
        self._endpoint = endpoint
        self._bridge   = bridge          # None until the Notes token is provisioned: approved-note work is refused
        self._activity = activity

    # -- IAgentAdapter ------------------------------------------------------------

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = NotesRequest.model_validate( json.loads( task.prompt ))
        except ( ValueError, ValidationError ):
            self._log( task, "refused", detail="the prompt is not a notes request" )
            return self._refused( 'a notes task is JSON: {"notes": "list"} | {"notes": "read", "id": "<filename>"} | '
                                  '{"notes": "update", "id": "<filename>", "expected_revision": "<from read>", '
                                  '"edits": [{"find": "<exact text>", "replace": "<text>"}]} | '
                                  '{"notes": "status" | "marker_write" | "marker_clear", "marker": "NOTESDIAG-<12 hex>"}' )
        if OPERATION[ request.notes ] != task.operation:
            return self._refused( f"{ request.notes } runs as { OPERATION[ request.notes ]}, not { task.operation }" )
        problem = request.problem()
        if problem is not None:
            self._log( task, "refused", filename=request.id or "", detail=problem )
            return self._refused( problem )
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        if request.notes in ( "list", "read", "update" ):
            return self._approved( task, request )
        try:
            if request.notes == "status":
                return self._status( request.marker )
            if request.notes == "marker_write":
                return self._write( task, request.marker )
            return self._clear()
        except NoteChanged as exc:
            return self._failed( f"the diagnostic note changed while this task ran, so nothing was written ({ exc })" )
        except OSError as exc:
            return self._failed( f"the Notes store could not be used: { type( exc ).__name__ }" )

    # -- operations ---------------------------------------------------------------

    def _status( self, marker: str | None ) -> AgentOutcome:
        mine  = self._owned()
        trace = [ self._trace( name, marker ) for name in mine ]
        found = f"{ len( mine )} diagnostic note(s)" if mine else "no diagnostic note"
        line  = f"Notes store holds { len( self._store.names())} note(s); { found }."
        if marker and trace:
            seen = trace[ 0 ]
            line += ( f" Marker { marker }: in the store { _yes( seen[ 'store_marker' ])}, "
                      f"on the served endpoint { _yes( seen[ 'served_marker' ])}, "
                      f"revisions { 'agree' if seen[ 'revisions_agree' ] else 'DISAGREE' }." )
        return self._done( line, { "notes": "status", "marker": marker, "notes_total": len( self._store.names()),
                                   "diagnostic": trace })

    def _write( self, task: ClaimedTask, marker: str ) -> AgentOutcome:
        text = f"{ HEADER }\n\nMarker: { marker }\nWritten by Bridge task { task.task_id } (attempt { task.attempt }).\n"
        mine = self._owned()
        if mine:
            filename = mine[ 0 ]
            before   = self._store.read( filename )
            if before is None:
                raise NoteChanged( "it disappeared between list and read" )
            revision_before = before[ 1 ]
            if before[ 0 ] == text:
                revision_after, created = revision_before, False
            else:
                revision_after, created = self._store.write( filename, text, revision_before ), False
        else:
            filename, revision_after = self._store.create( STEM, text )
            revision_before, created = None, True
        trace = self._trace( filename, marker )
        ok    = trace[ "store_marker" ] and trace[ "served_marker" ] and trace[ "revisions_agree" ] \
                and trace[ "store_revision" ] == revision_after
        details = { "notes": "marker_write", "marker": marker, "filename": filename, "created": created,
                    "revision_before": revision_before, "revision_after": revision_after, "read_back_ok": ok, **trace }
        line = ( f"Marker { marker } { 'created in' if created else 'written to' } { filename }; "
                 f"read back from the store { _yes( trace[ 'store_marker' ])} and from the served endpoint "
                 f"{ _yes( trace[ 'served_marker' ])} at revision { revision_after[ :12 ]}." )
        return self._done( line, details ) if ok else self._failed( "read-back failed: " + line, details )

    def _clear( self ) -> AgentOutcome:
        removed: list[ dict ] = []
        for filename in self._owned():
            current = self._store.read( filename )
            if current is None:
                continue
            self._store.delete( filename, current[ 1 ])
            removed.append({ "filename": filename, "revision": current[ 1 ],
                             "gone_from_store": self._store.read( filename ) is None,
                             "gone_from_endpoint": self._endpoint.served( filename ) is None })
        ok   = all( r[ "gone_from_store" ] and r[ "gone_from_endpoint" ] for r in removed )
        line = ( f"Removed { len( removed )} diagnostic note(s): { ', '.join( r[ 'filename' ] for r in removed )}; "
                 f"gone from the store and the served endpoint { _yes( ok )}." if removed
                 else "There was no diagnostic note to remove." )
        details = { "notes": "marker_clear", "removed": removed, "notes_total": len( self._store.names())}
        return self._done( line, details ) if ok else self._failed( line, details )

    # -- approved notes, through the dashboard's authorized interface --------------

    def _approved( self, task: ClaimedTask, request: NotesRequest ) -> AgentOutcome:
        if self._bridge is None:
            self._log( task, "auth_failed", filename=request.id or "", detail="no Notes token is provisioned on this machine" )
            return self._refused( "the Notes interface token is not provisioned on this machine" )
        try:
            if request.notes == "list":
                names = self._bridge.list( task.task_id )
                return self._done( f"{ len( names )} approved note(s): { ', '.join( names ) or 'none' }.",
                                   { "notes": "list", "approved": names }, body=json.dumps({ "approved": names }))
            if request.notes == "read":
                text, revision = self._bridge.read( request.id, task.task_id )
                return self._done( f"Read { request.id } at revision { revision[ :12 ]} ({ len( text.encode( 'utf-8' ))} bytes).",
                                   { "notes": "read", "id": request.id, "revision": revision },
                                   body=json.dumps({ "id": request.id, "revision": revision, "note": text }))
            return self._update( task, request )
        except NotesDenied as exc:
            self._log( task, "auth_failed", filename=request.id or "", detail=f"the Notes interface refused: { exc }" )
            return self._refused( f"the Notes interface refused { request.notes } of { request.id or 'the list' }: { exc }" )
        except NoteChanged as exc:
            return self._conflict( task, request, str( exc ))
        except FileNotFoundError:
            self._log( task, "error", filename=request.id or "", detail="no such note" )
            return self._failed( f"there is no note { request.id }" )
        except OSError as exc:
            self._log( task, "error", filename=request.id or "", detail=type( exc ).__name__ )
            return self._failed( f"the Notes interface could not be used: { exc }" )

    def _update( self, task: ClaimedTask, request: NotesRequest ) -> AgentOutcome:
        current, revision = self._bridge.read( request.id, task.task_id )
        if revision != request.expected_revision:
            return self._conflict( task, request, f"it is at { revision[ :12 ]}, not { request.expected_revision[ :12 ]}",
                                   revision )
        if request.text is not None:
            wanted = request.text
        else:
            wanted = current
            for number, edit in enumerate( request.edits, start=1 ):
                count = wanted.count( edit.find )
                if count != 1:
                    why = "is not in the note" if count == 0 else f"occurs { count } times"
                    self._log( task, "refused", filename=request.id, revision=revision,
                               detail=f"edit { number }: its find text { why }" )
                    return self._refused( f"edit { number }: its find text { why }, so nothing was written "
                                          f"(the note is still at { revision[ :12 ]})" )
                wanted = wanted.replace( edit.find, edit.replace, 1 )
        wanted = wanted if wanted.endswith( "\n" ) else f"{ wanted }\n"
        if wanted == current:
            self._log( task, "unchanged", filename=request.id, revision=revision )
            return self._done( f"{ request.id } already reads that way; nothing written (revision { revision[ :12 ]}).",
                               { "notes": "update", "id": request.id, "revision_before": revision,
                                 "revision_after": revision, "changed": False, "verified": True })
        after = self._bridge.write( request.id, wanted, revision, task.task_id )
        return self._verify( task, request, current, wanted, revision, after )

    def _verify( self, task: ClaimedTask, request: NotesRequest, before: str, wanted: str, revision_before: str,
                 revision_after: str ) -> AgentOutcome:
        text_back, rev_back = self._bridge.read( request.id, task.task_id )
        served             = self._endpoint.served( request.id )
        checks = { "interface_text_matches": text_back == wanted, "interface_revision_matches": rev_back == revision_after,
                   "dashboard_text_matches": bool( served and served[ 0 ] == wanted ),
                   "dashboard_revision_matches": bool( served and served[ 1 ] == revision_after ),
                   "listed_on_dashboard": request.id in self._endpoint.listed()}
        ok   = all( checks.values())
        diff = "".join( difflib.unified_diff( before.splitlines( keepends=True ), wanted.splitlines( keepends=True ),
                                              fromfile=f"{ request.id }@{ revision_before[ :12 ]}",
                                              tofile=f"{ request.id }@{ revision_after[ :12 ]}", n=1 ))
        details = { "notes": "update", "id": request.id, "revision_before": revision_before,
                    "revision_after": revision_after, "changed": True, "verified": ok,
                    "edits": len( request.edits or []), "bytes": len( wanted.encode( "utf-8" )), **checks }
        self._log( task, "verified" if ok else "verify_failed", filename=request.id, revision=revision_after,
                   expected_revision=revision_before,
                   detail="read back through the interface and the dashboard" if ok
                          else "read-back mismatch: " + ", ".join( k for k, v in checks.items() if not v ))
        line = ( f"Updated { request.id }: revision { revision_before[ :12 ]} -> { revision_after[ :12 ]}; "
                 f"read back through the Notes interface and the dashboard's own endpoint { _yes( ok )}." )
        body = json.dumps({ "id": request.id, "revision": revision_after, "verified": ok, "diff": diff })
        return self._done( line, details, body=body ) if ok else self._failed( "read-back failed: " + line, details )

    def _conflict( self, task: ClaimedTask, request: NotesRequest, why: str, current: str | None = None ) -> AgentOutcome:
        self._log( task, "conflict", filename=request.id or "", expected_revision=request.expected_revision or "",
                   revision=current or "", detail=why )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, retryable=False,
                             summary=f"{ request.id } changed since it was read ({ why }); nothing was written. "
                                     f"Read it again and resend the update with the new revision.",
                             details={ "notes": request.notes, "id": request.id, "conflict": True,
                                       "expected_revision": request.expected_revision, "current_revision": current })

    def _log( self, task: ClaimedTask, event: str, **fields: str ) -> None:
        if self._activity is not None:
            self._activity.record( event, task_id=task.task_id, op=_op( task ), **fields )

    # -- evidence -----------------------------------------------------------------

    def _owned( self ) -> list[ str ]:
        """Names that are this agent's by name *and* by header. Anything else is never touched."""
        out = []
        for name in self._store.names():
            if not OWNED.match( name ):
                continue
            current = self._store.read( name )
            if current is not None and current[ 0 ].startswith( HEADER ):
                out.append( name )
        return out

    def _trace( self, filename: str, marker: str | None ) -> dict:
        stored = self._store.read( filename )
        served = self._endpoint.served( filename )
        has    = ( lambda text: bool( marker ) and marker in text )
        return { "filename": filename,
                 "store_exists": stored is not None, "store_revision": stored[ 1 ] if stored else None,
                 "store_bytes": len( stored[ 0 ].encode( "utf-8" )) if stored else 0,
                 "store_marker": bool( stored and has( stored[ 0 ])),
                 "served_exists": served is not None, "served_revision": served[ 1 ] if served else None,
                 "served_marker": bool( served and has( served[ 0 ])),
                 "listed": filename in self._endpoint.listed(),
                 "revisions_agree": bool( stored and served and stored[ 1 ] == served[ 1 ])}

    # -- outcomes -----------------------------------------------------------------

    @staticmethod
    def _done( summary: str, details: dict, body: str | None = None ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=summary[ :300 ],
                             body=summary if body is None else body, details=details )

    @staticmethod
    def _failed( summary: str, details: dict | None = None ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary,
                             retryable=False, details=details )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )


def _op( task: ClaimedTask ) -> str:
    try:
        return str( json.loads( task.prompt ).get( "notes", "" ))[ :20 ]
    except ( ValueError, AttributeError ):
        return ""


def _yes( flag: bool ) -> str:
    return "yes" if flag else "NO"

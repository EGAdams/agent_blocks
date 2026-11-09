"""NotesAgent: the IAgentAdapter that reads and updates dashboard Notes for ChatGPT. No model in the path.

On Nov 4 Bridge agents reported Notes changes that never reached the page:
codex and codex-writer are jailed to the `letta_code` checkout, and the
dashboard's Notes page reads Markdown from `NOTES_DIR` (`~/notes`), which no
Bridge workspace contains. This agent is the one Bridge path that reaches that
store, and it reaches only its own diagnostic note:

    inspect     read_only        {"notes": "status", "marker": "NOTESDIAG-..."?}
    implement   workspace_write  {"notes": "marker_write", "marker": "NOTESDIAG-..."}
    implement   workspace_write  {"notes": "marker_clear"}

ChatGPT and Toyota read, search and manage every note by ID (the note's filename) through
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

Every operation is a Command object (`notes_ops/commands.py`) found in a CommandRegistry; this class is the
Facade that validates a prompt, picks the Command, and turns what can go wrong into one honest outcome:

    inspect    list [query, category] | search | read | highlights | trash_list | refresh (id, known_revision)
               review_inspect | review_get_active_highlight | review_verify | review_snapshots | review_watch
    implement  update (edits: find / line / section, or text) | highlight (marks: add / recolor / clear)
               create | rename (new_name, category 0-10; 0 = none) | trash | restore | purge
               purge is permanent: only from the trash, only with the exact authorization sentence.
               review_stop_blink | review_activate | review_activate_next | review_edit | review_mark_resolved |
               review_rollback

The review_* operations (notes_ops/review.py) are what blinks on a note and why: each is one run of the Notes
Review ADW (adws/adw_notes_review.py), which applies its own guards and keeps an undo point for every write.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .notes_ops.base import (INotesActivity, INotesBridge, INotesEndpoint, INotesGateway, NoteChanged, NotesContext,
                             NotesDenied, NotesRefusal, conflict, done, failed, refused, yes)
from .notes_ops.commands import CommandRegistry, default_registry
from .notes_ops.requests import MARKER, NOTE_ID, REVISION, HighlightChange, NoteEdit, NotesRequest   # noqa: F401  (re-exported)
from .ports import Signal

PERFORMER = "notes"
HEADER    = "# Bridge Notes diagnostic"
STEM      = "bridge-notes-diagnostic"
OWNED     = re.compile( rf"^\d{{4}}-\d{{2}}-\d{{2}}_{ STEM }(-\d+)?\.md$" )
DIAGNOSTIC = { "status": "inspect", "marker_write": "implement", "marker_clear": "implement" }


class INotesStore( Protocol ):
    """The Markdown store the dashboard's Notes page reads (NOTES_DIR)."""

    def names( self ) -> list[ str ]: ...                                 # every *.md, newest first

    def read( self, filename: str ) -> tuple[ str, str ] | None: ...      # ( text, revision ), None if absent

    def create( self, stem: str, text: str ) -> tuple[ str, str ]: ...    # a new dated, unique file: ( filename, revision )

    def write( self, filename: str, text: str, expected_revision: str ) -> str: ...   # NoteChanged on a mismatch

    def delete( self, filename: str, expected_revision: str ) -> None: ...          # NoteChanged on a mismatch


class NotesAgent:
    def __init__( self, store: INotesStore, endpoint: INotesEndpoint, bridge: INotesGateway | None = None,
                  activity: INotesActivity | None = None, commands: CommandRegistry | None = None ) -> None:
        self._store    = store
        self._endpoint = endpoint
        self._bridge   = bridge          # None until the Notes token is provisioned: note work is refused
        self._activity = activity
        self._commands = commands or default_registry()

    # -- IAgentAdapter ------------------------------------------------------------

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = NotesRequest.model_validate( json.loads( task.prompt ))
        except ( ValueError, ValidationError ):
            self._log( task, "refused", detail="the prompt is not a notes request" )
            return self._refused( 'a notes task is JSON, e.g. {"notes": "list"} | {"notes": "read", "id": "<filename>"} | '
                                  '{"notes": "update", "id": "<filename>", "expected_revision": "<from read>", '
                                  '"edits": [{"find": "<exact text>", "replace": "<text>"}]} ; operations: '
                                  + ", ".join( list( DIAGNOSTIC ) + self._commands.names()))
        command = self._commands.get( request.notes )
        if command is None and request.notes not in DIAGNOSTIC:
            return self._refused( f"{ request.notes } is not offered by this worker (the Notes Review ADW is not wired here)" )
        wanted_operation = DIAGNOSTIC.get( request.notes ) or ( command.operation if command else "" )
        if wanted_operation != task.operation:
            return self._refused( f"{ request.notes } runs as { wanted_operation }, not { task.operation }" )
        problem = command.problem( request ) if command else self._diagnostic_problem( request )
        if problem is not None:
            self._log( task, "refused", filename=request.id or "", detail=problem )
            return self._refused( problem )
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        if command is not None:
            return self._invoke( command, task, request, keep_alive )
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

    @staticmethod
    def _diagnostic_problem( request: NotesRequest ) -> str | None:
        if request.given() - { "marker" }:
            return f"{ request.notes } takes no id, revision, edits or text"
        if request.notes == "marker_write" and request.marker is None:
            return "marker_write needs a marker"
        return None

    def _invoke( self, command, task: ClaimedTask, request: NotesRequest, keep_alive=None ) -> AgentOutcome:
        """Run one Command, and turn every way it can fail into one outcome (the Facade's job)."""
        if self._bridge is None:
            self._log( task, "auth_failed", filename=request.id or "", detail="no Notes token is provisioned on this machine" )
            return self._refused( "the Notes interface token is not provisioned on this machine" )
        ctx = NotesContext( self._bridge, self._endpoint, self._activity, keep_alive )
        try:
            return command.execute( ctx, task, request )
        except NotesDenied as exc:
            self._log( task, "auth_failed", filename=request.id or "", detail=f"the Notes interface refused: { exc }" )
            return self._refused( f"the Notes interface refused { request.notes } of { request.id or request.trash_id or 'the notes' }: { exc }" )
        except NoteChanged as exc:
            return conflict( ctx, task, request, str( exc ))
        except NotesRefusal as exc:
            self._log( task, "refused", filename=request.id or "", detail=str( exc )[ :200 ] )
            return self._refused( str( exc ))
        except FileNotFoundError:
            self._log( task, "error", filename=request.id or "", detail="no such note" )
            return self._failed( f"there is no note { request.id or request.trash_id }" )
        except OSError as exc:
            self._log( task, "error", filename=request.id or "", detail=type( exc ).__name__ )
            return self._failed( f"the Notes interface could not be used: { exc }" )

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
    # -- outcomes -----------------------------------------------------------------

    _done    = staticmethod( done )
    _failed  = staticmethod( failed )
    _refused = staticmethod( refused )


def _op( task: ClaimedTask ) -> str:
    try:
        return str( json.loads( task.prompt ).get( "notes", "" ))[ :20 ]
    except ( ValueError, AttributeError ):
        return ""


_yes = yes

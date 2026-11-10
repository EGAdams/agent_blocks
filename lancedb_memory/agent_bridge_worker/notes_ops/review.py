"""The review operations on a note's highlights -- what blinks, stop it, edit it, resolve it, move on -- as Commands.

Each one is a thin Command over the Notes Review ADW (`adws/adw_notes_review.py`, package `adws/notes_review/`),
reached through `INotesReviewCommands`. This worker does not re-implement any of it: the ADW reads the note through
the same token-protected Notes interface, applies its five guards (revision read, no other line changes, evidence or
approval to resolve, an edit may not quietly resolve an item, a still-open line cannot be marked resolved), keeps an
undo snapshot, reads the write back, and -- with `page` -- reloads the dashboard to check it shows the same thing.
Here a request is only checked for shape and turned into flags; the ADW's one JSON answer becomes the outcome, with
its own ADW id.

    inspect    review_inspect | review_get_active_highlight | review_verify | review_snapshots | review_watch
    implement  review_stop_blink | review_activate | review_activate_next | review_edit | review_mark_resolved
               review_rollback
    attach     review_attachments | review_attachment (inspect); review_attach_pdf | review_detach_pdf (implement)

A PDF reaches review_attach_pdf through the gateway, never a shared path: ChatGPT uploads it there (notes_upload_*),
`AttachPdfCommand` fetches it for this task alone (`IUploadSource`, under the task's lease), checks its sha256, hands it
to the ADW as a private 0600 temp file, and deletes the file when the ADW has answered.

The public names (ChatGPT's `notes_inspect` tool, the ADW's `notes.inspect`) differ only in spelling.
"""
from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..models import AgentOutcome, ClaimedTask
from .base import NotesContext, done, failed, said_body
from .commands import NotesCommand
from .requests import NotesRequest


class ReviewCommandFailed( Exception ):
    """The Notes Review ADW gave no answer (it crashed, timed out, or wrote nothing)."""


class INotesReviewCommands( Protocol ):
    """Runs one notes.* operation of the review ADW and returns its JSON answer ({ok, line, data})."""

    def run( self, operation: str, flags: dict[ str, str ], switches: list[ str ], timeout: float,
             beat: Callable[[], str ], stoppable: bool ) -> dict: ...
    # `beat` is called every few seconds while the ADW runs (it keeps the task's lease). A read (`stoppable`) is
    # stopped when it answers anything but "continue"; a write that has started always runs to its end.


class IUploadSource( Protocol ):
    """The PDF a client uploaded for this task, fetched under the task's lease ({sha256, bytes, name, pdf_base64})."""

    def fetch_upload( self, task: ClaimedTask ) -> dict: ...


#: request field -> the ADW flag it becomes (values), and the request fields that are on/off switches.
VALUE_FLAGS = { "item_id": "item-id", "replace": "replace", "evidence": "evidence", "approval": "approval",
                "expected_revision": "expected-revision", "expect_item_id": "expect-item-id",
                "snapshot_id": "snapshot-id", "until": "until", "timeout": "timeout", "interval": "interval",
                "limit": "limit", "attachment_id": "attachment-id", "title": "title",
                "upload_sha256": "upload-sha256", "upload_name": "name" }
#: flags a Command supplies itself (never from a request): the private temp file holding an uploaded PDF.
OWN_FLAGS   = ( "pdf-file", )
SWITCHES    = { "advance": "advance", "page": "page", "no_page": "no-page", "chain": "chain" }
#: fields every answer may carry back; the ADW already left out nothing a caller should not see.
DETAIL_KEYS = ( "note", "revision", "active_item_id", "active_text", "active_line", "highlight_status", "pending",
                "pending_count", "focus", "verification", "snapshot_id", "undo_snapshot_id", "new_item_id", "item_id", "cause",
                "byte_for_byte", "restored_revision", "undid", "snapshots", "met", "polls", "until", "watched_item_id",
                "before_revision", "after_revision", "reason", "adw_id",
                "attachment", "attachments", "attachment_id", "created", "removed", "note_unchanged", "verified",
                "problems", "revision_before" )


class ReviewCommand( NotesCommand ):
    """One review operation. `public` is the ADW's name for it; the rest is a table entry, not a subclass."""

    def __init__( self, name: str, public: str, operation: str, required: frozenset[ str ], allowed: frozenset[ str ],
                  runner: INotesReviewCommands, seconds: float = 150.0 ) -> None:
        self.name, self.public, self.operation = name, public, operation
        self.required = frozenset({ "id" }) | required
        self.allowed  = allowed
        self._runner, self._seconds = runner, seconds

    def shape_problem( self, request: NotesRequest ) -> str | None:
        if self.public == "notes.mark_resolved" and not ( request.evidence or request.approval ):
            return "review_mark_resolved needs evidence (what record shows it) or approval (what EG said), in words"
        if self.public == "notes.watch" and request.until is None:
            return "review_watch needs until: clear, change or active_change"
        return None

    def execute( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest ) -> AgentOutcome:
        return self._run_adw( ctx, task, request, {})

    def _run_adw( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest,
                  own: dict[ str, str ]) -> AgentOutcome:
        """Turn the request into flags (plus the Command's `own` flags), run the ADW once, and report its answer."""
        flags = { "note": request.id or "", **own }
        for field, flag in VALUE_FLAGS.items():
            value = getattr( request, field )
            if value is not None:
                flags[ flag ] = str( value )
        switches = [ flag for field, flag in SWITCHES.items() if getattr( request, field ) is True ]
        seconds  = self._seconds + ( float( request.timeout or 60 ) if self.public == "notes.watch" else 0.0 )
        try:
            answer = self._runner.run( self.public, flags, switches, seconds, ctx.keep_alive, self.operation == "inspect" )
        except ReviewCommandFailed as exc:
            ctx.log( task, "error", filename=request.id or "", detail=f"review ADW: { exc }"[ :200 ] )
            return failed( f"the Notes Review ADW gave no answer ({ exc }); nothing is known to have changed",
                           { "notes": request.notes, "id": request.id })
        data    = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line    = str( answer.get( "line" ) or "" )[ :1500 ]
        details = { "notes": request.notes, "id": request.id, "ok": bool( answer.get( "ok" )),
                    **{ k: data[ k ] for k in DETAIL_KEYS if k in data }}
        ctx.log( task, "review" if details[ "ok" ] else "refused", filename=request.id or "",
                 detail=f"{ self.public }: { line }"[ :200 ] )
        body = said_body( line, **{ k: v for k, v in details.items() if k not in ( "notes", "ok" )})
        if details[ "ok" ]:
            return done( line, details, body=body )
        out = failed( line or f"{ self.public } did not complete", details )
        return out.model_copy( update={ "body": body })


def _set( *names: str ) -> frozenset[ str ]:
    return frozenset( names )


class AttachPdfCommand( ReviewCommand ):
    """review_attach_pdf: fetch the uploaded PDF for this task, check it is the one the gateway described, give it to
    the ADW as a private temp file, and always delete that file afterwards."""

    def __init__( self, name: str, public: str, operation: str, required: frozenset[ str ], allowed: frozenset[ str ],
                  runner: INotesReviewCommands, uploads: IUploadSource, workdir: Path ) -> None:
        super().__init__( name, public, operation, required, allowed, runner )
        self._uploads, self._workdir = uploads, workdir

    def shape_problem( self, request: NotesRequest ) -> str | None:
        if not ( request.upload_sha256 and request.upload_bytes ):
            return "review_attach_pdf comes through notes_attach_pdf, which describes the upload; it did not"
        return None

    def execute( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest ) -> AgentOutcome:
        failure = { "notes": request.notes, "id": request.id, "upload_id": request.upload_id }
        try:
            upload = self._uploads.fetch_upload( task )
            data   = base64.b64decode( str( upload.get( "pdf_base64" ) or "" ), validate=True )
        except Exception as exc:                              # GatewayError, ValueError: nothing was attached
            ctx.log( task, "refused", filename=request.id or "", detail=f"upload fetch: { type( exc ).__name__ }"[ :200 ])
            return failed( f"the uploaded PDF could not be fetched from the gateway ({ type( exc ).__name__ }: "
                           f"{ str( exc )[ :160 ]}); nothing was attached", failure )
        sha = hashlib.sha256( data ).hexdigest()
        if upload.get( "upload_id" ) != request.upload_id or sha != request.upload_sha256 or len( data ) != request.upload_bytes:
            ctx.log( task, "refused", filename=request.id or "", detail="upload fetch: not the upload described" )
            return failed( "the gateway sent a PDF that is not the upload this task describes (id, size or sha256 "
                           "differ); nothing was attached", failure )
        self._workdir.mkdir( mode=0o700, parents=True, exist_ok=True )
        fd, path = tempfile.mkstemp( prefix="attach_", suffix=".pdf", dir=self._workdir )   # 0600
        try:
            with os.fdopen( fd, "wb" ) as out:
                out.write( data )
            return self._run_adw( ctx, task, request, { "pdf-file": path })
        finally:
            Path( path ).unlink( missing_ok=True )


#: name -> ( ADW operation, task operation, required besides id, allowed )
TABLE = {
    "review_inspect":              ( "notes.inspect", "inspect", _set(), _set()),
    "review_get_active_highlight": ( "notes.get_active_highlight", "inspect", _set(), _set( "page" )),
    "review_verify":               ( "notes.verify", "inspect", _set(), _set( "no_page", "expect_item_id" )),
    "review_snapshots":            ( "notes.snapshots", "inspect", _set(), _set( "limit" )),
    "review_watch":                ( "notes.watch", "inspect", _set(), _set( "until", "timeout", "interval", "item_id", "page" )),
    "review_stop_blink":           ( "notes.stop_blink", "implement", _set(), _set( "page", "expected_revision" )),
    "review_activate":             ( "notes.activate", "implement", _set( "item_id" ), _set( "page", "expected_revision" )),
    "review_activate_next":        ( "notes.activate_next", "implement", _set(), _set( "item_id", "page", "expected_revision" )),
    "review_edit":                 ( "notes.edit", "implement", _set( "item_id", "replace", "expected_revision" ), _set( "page" )),
    "review_mark_resolved":        ( "notes.mark_resolved", "implement", _set( "item_id", "expected_revision" ),
                                     _set( "replace", "evidence", "approval", "advance", "page" )),
    "review_rollback":             ( "notes.rollback", "implement", _set( "snapshot_id", "expected_revision" ), _set( "chain", "page" )),
    "review_attachments":          ( "notes.attachments", "inspect", _set(), _set()),
    "review_attachment":           ( "notes.attachment", "inspect", _set( "attachment_id" ), _set()),
    "review_detach_pdf":           ( "notes.detach_pdf", "implement", _set( "attachment_id" ), _set()),
    "review_attach_pdf":           ( "notes.attach_pdf", "implement", _set( "upload_id", "title" ),
                                     _set( "upload_sha256", "upload_bytes", "upload_name" )),
}
REVIEW_OPERATIONS = tuple( TABLE )
#: operations that need an uploaded file, so they are offered only when an IUploadSource is wired.
NEEDS_UPLOAD = { "review_attach_pdf": AttachPdfCommand }


def review_commands( runner: INotesReviewCommands, uploads: IUploadSource | None = None,
                     workdir: Path | None = None ) -> list[ ReviewCommand ]:
    commands: list[ ReviewCommand ] = []
    for name, ( public, operation, required, allowed ) in TABLE.items():
        if name in NEEDS_UPLOAD:
            if uploads is not None:
                commands.append( NEEDS_UPLOAD[ name ]( name, public, operation, required, allowed, runner, uploads,
                                                       workdir or Path( tempfile.gettempdir()) / "notes_attach" ))
            continue
        commands.append( ReviewCommand( name, public, operation, required, allowed, runner ))
    return commands


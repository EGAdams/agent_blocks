"""INotesCommand Commands -- one object per notes operation -- and the CommandRegistry that finds them.

Pattern map
    Command            every operation is an object with `problem()` (is this a request?) and `execute()`
    Template Method    `VerifiedWrite` fixes the order  read -> check revision -> transform -> write -> read back
                       twice; `UpdateNote` and `ChangeHighlights` supply only `transform()`.
                       `Lifecycle` fixes  perform -> verify -> outcome; create/rename/trash/restore/purge supply
                       `perform()` and `verify()`.
    Strategy / Factory the text changes themselves are INoteEdit / IHighlightChange (edits.py, highlights.py)
    Registry           `CommandRegistry` replaces an if-chain; adding an operation is one `register()` call

Nothing here talks HTTP or files: it all goes through NotesContext, so it runs offline against fakes.
"""
from __future__ import annotations

import difflib
import json
from abc import ABC, abstractmethod

from ..models import AgentOutcome, ClaimedTask
from .base import NotesContext, NotesRefusal, conflict, done, failed, refused, yes
from .edits import edit_from
from .highlights import SPAN, highlight_from
from .names import create_hint, renamed
from .requests import NotesRequest

_NEEDS = {
    "id":                "the note's id (its filename, from list)",
    "expected_revision": "expected_revision: the revision a read of this note returned",
    "known_revision":    "known_revision: the revision you last read",
    "trash_id":          "trash_id (from trash_list)",
    "name":              "a name",
    "text":              "text",
    "authorization":     "authorization: the exact sentence EG's permission is recorded as",
    "query":             "a query",
}


class NotesCommand( ABC ):
    name:      str
    operation: str                       # "inspect" (read only) or "implement" (may write)
    allowed:   frozenset[ str ] = frozenset()
    required:  frozenset[ str ] = frozenset()

    def problem( self, request: NotesRequest ) -> str | None:
        given = request.given()
        extra = given - self.allowed - self.required
        if extra:
            return f"{ self.name } takes no { ', '.join( sorted( extra ))}" if not ( self.allowed | self.required ) \
                else f"{ self.name } does not take { ', '.join( sorted( extra ))}"
        for field in sorted( self.required - given ):
            return f"{ self.name } needs { _NEEDS.get( field, field )}"
        return self.shape_problem( request )

    def shape_problem( self, request: NotesRequest ) -> str | None:
        return None

    @abstractmethod
    def execute( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest ) -> AgentOutcome: ...


# -- inspect -----------------------------------------------------------------------------------------

class ListNotes( NotesCommand ):
    name, operation = "list", "inspect"
    allowed = frozenset({ "query", "category" })

    def execute( self, ctx, task, request ):
        if request.query is None and request.category is None:
            names = ctx.gateway.list( task.task_id )
            return done( f"{ len( names )} note(s): { ', '.join( names ) or 'none' }.",
                         { "notes": "list", "approved": names }, body=json.dumps({ "approved": names }))
        entries = ctx.gateway.entries( request.query or "", request.category, task.task_id )
        names   = [ e[ "filename" ] for e in entries ]
        what    = f'matching "{ request.query }"' if request.query else f"in category { request.category }"
        return done( f"{ len( names )} note(s) { what }: { ', '.join( names ) or 'none' }.",
                     { "notes": "list", "count": len( names )}, body=json.dumps({ "approved": names, "entries": entries }))


class SearchNotes( ListNotes ):
    name, allowed, required = "search", frozenset({ "category" }), frozenset({ "query" })


class ReadNote( NotesCommand ):
    name, operation, required = "read", "inspect", frozenset({ "id" })

    def execute( self, ctx, task, request ):
        text, revision = ctx.gateway.read( request.id, task.task_id )
        return done( f"Read { request.id } at revision { revision[ :12 ]} ({ len( text.encode( 'utf-8' ))} bytes).",
                     { "notes": "read", "id": request.id, "revision": revision },
                     body=json.dumps({ "id": request.id, "revision": revision, "note": text }))


class ReadHighlights( NotesCommand ):
    """Persisted ``{{highlight}}`` spans (exact text, line, offsets) and the deed page's review marks."""
    name, operation, required = "highlights", "inspect", frozenset({ "id" })

    def execute( self, ctx, task, request ):
        got = ctx.gateway.highlights( request.id, task.task_id )
        spans, marks = got.get( "highlights", [] ), got.get( "review_marks", [] )
        return done( f"{ request.id } has { len( spans )} saved highlight(s) and { len( marks )} review mark(s) "
                     f"at revision { str( got.get( 'revision', '' ))[ :12 ]}.",
                     { "notes": "highlights", "id": request.id, "highlights": len( spans ), "review_marks": len( marks )},
                     body=json.dumps({ "id": request.id, **got }))


class ListTrash( NotesCommand ):
    name, operation = "trash_list", "inspect"

    def execute( self, ctx, task, request ):
        items = ctx.gateway.trash_list( task.task_id )
        return done( f"{ len( items )} note(s) in the trash.", { "notes": "trash_list", "count": len( items )},
                     body=json.dumps({ "trash": items }))


class RefreshNote( NotesCommand ):
    """Has someone else changed this note since I read it? Returns the new text only if so."""
    name, operation, required = "refresh", "inspect", frozenset({ "id", "known_revision" })

    def execute( self, ctx, task, request ):
        text, revision = ctx.gateway.read( request.id, task.task_id )
        changed = revision != request.known_revision
        body = { "id": request.id, "revision": revision, "changed": changed, **({ "note": text } if changed else {}) }
        return done( f"{ request.id } " + ( f"changed externally: now at { revision[ :12 ]}." if changed
                                             else f"is unchanged at { revision[ :12 ]}." ),
                     { "notes": "refresh", "id": request.id, "revision": revision, "changed": changed }, body=json.dumps( body ))


# -- implement: verified overwrite (Template Method) ----------------------------------------------------

class VerifiedWrite( NotesCommand ):
    operation = "implement"
    required  = frozenset({ "id", "expected_revision" })

    @abstractmethod
    def transform( self, current: str, request: NotesRequest ) -> str: ...

    def extra_checks( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest, wanted: str,
                      revision_after: str ) -> dict[ str, bool ]:
        return {}

    def execute( self, ctx, task, request ):
        current, revision = ctx.gateway.read( request.id, task.task_id )
        if revision != request.expected_revision:
            return conflict( ctx, task, request, f"it is at { revision[ :12 ]}, not { request.expected_revision[ :12 ]}", revision )
        try:
            wanted = self.transform( current, request )
        except NotesRefusal as exc:
            ctx.log( task, "refused", filename=request.id, revision=revision, detail=str( exc )[ :200 ])
            return refused( f"{ exc }, so nothing was written (the note is still at { revision[ :12 ]})" )
        wanted = wanted if wanted.endswith( "\n" ) else f"{ wanted }\n"
        if wanted == current:
            ctx.log( task, "unchanged", filename=request.id, revision=revision )
            return done( f"{ request.id } already reads that way; nothing written (revision { revision[ :12 ]}).",
                         { "notes": request.notes, "id": request.id, "revision_before": revision,
                           "revision_after": revision, "changed": False, "verified": True })
        after = ctx.gateway.write( request.id, wanted, revision, task.task_id )
        return self._verify( ctx, task, request, current, wanted, revision, after )

    def _verify( self, ctx, task, request, before, wanted, revision_before, revision_after ):
        text_back, rev_back = ctx.gateway.read( request.id, task.task_id )
        served = ctx.endpoint.served( request.id )
        checks = { "interface_text_matches": text_back == wanted, "interface_revision_matches": rev_back == revision_after,
                   "dashboard_text_matches": bool( served and served[ 0 ] == wanted ),
                   "dashboard_revision_matches": bool( served and served[ 1 ] == revision_after ),
                   "listed_on_dashboard": request.id in ctx.endpoint.listed(),
                   **self.extra_checks( ctx, task, request, wanted, revision_after )}
        ok   = all( checks.values())
        diff = "".join( difflib.unified_diff( before.splitlines( keepends=True ), wanted.splitlines( keepends=True ),
                                              fromfile=f"{ request.id }@{ revision_before[ :12 ]}",
                                              tofile=f"{ request.id }@{ revision_after[ :12 ]}", n=1 ))
        details = { "notes": request.notes, "id": request.id, "revision_before": revision_before,
                    "revision_after": revision_after, "changed": True, "verified": ok,
                    "edits": len( request.edits or request.marks or []), "bytes": len( wanted.encode( "utf-8" )), **checks }
        ctx.log( task, "verified" if ok else "verify_failed", filename=request.id, revision=revision_after,
                 expected_revision=revision_before,
                 detail="read back through the interface and the dashboard" if ok
                        else "read-back mismatch: " + ", ".join( k for k, v in checks.items() if not v ))
        line = ( f"Updated { request.id }: revision { revision_before[ :12 ]} -> { revision_after[ :12 ]}; "
                 f"read back through the Notes interface and the dashboard's own endpoint { yes( ok )}." )
        body = json.dumps({ "id": request.id, "revision": revision_after, "verified": ok, "diff": diff })
        return done( line, details, body=body ) if ok else failed( "read-back failed: " + line, details )


class UpdateNote( VerifiedWrite ):
    """Edit lines, sections or exact phrases -- or replace the whole text."""
    name, allowed = "update", frozenset({ "edits", "text" })

    def shape_problem( self, request ):
        if ( request.edits is None ) == ( request.text is None ):
            return "update takes exactly one of edits or text"
        if request.text is not None and not request.text.strip():
            return "update text must not be blank"
        return None

    def transform( self, current, request ):
        if request.text is not None:
            return request.text
        wanted = current
        for number, model in enumerate( request.edits, start=1 ):
            try:
                wanted = edit_from( model ).apply( wanted )
            except NotesRefusal as exc:
                raise NotesRefusal( f"edit { number }: { exc }" ) from None
        return wanted


class ChangeHighlights( VerifiedWrite ):
    """Add, recolor or clear highlights; the dashboard's own /highlights must agree afterwards."""
    name, allowed = "highlight", frozenset({ "marks" })
    required = VerifiedWrite.required | frozenset({ "marks" })

    def transform( self, current, request ):
        wanted = current
        for number, change in enumerate( request.marks, start=1 ):
            try:
                wanted = highlight_from( change ).apply( wanted )
            except NotesRefusal as exc:
                raise NotesRefusal( f"highlight change { number }: { exc }" ) from None
        return wanted

    def extra_checks( self, ctx, task, request, wanted, revision_after ):
        seen = ctx.gateway.highlights( request.id, task.task_id )
        expected = [ ( m.group( 1 ), m.group( 2 )) for m in SPAN.finditer( wanted )]
        return { "dashboard_highlights_match": seen.get( "revision" ) == revision_after and
                 [ ( h.get( "color" ), h.get( "text" )) for h in seen.get( "highlights", [])] == expected }


# -- implement: whole-note lifecycle (Template Method) --------------------------------------------------

class Lifecycle( NotesCommand ):
    operation = "implement"
    verb = ""

    @abstractmethod
    def perform( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest ) -> dict: ...

    @abstractmethod
    def verify( self, ctx: NotesContext, task: ClaimedTask, request: NotesRequest, result: dict ) -> dict[ str, bool ]: ...

    @abstractmethod
    def sentence( self, request: NotesRequest, result: dict ) -> str: ...

    def execute( self, ctx, task, request ):
        result = self.perform( ctx, task, request )
        checks = self.verify( ctx, task, request, result )
        ok = all( checks.values())
        ctx.log( task, "verified" if ok else "verify_failed", filename=str( result.get( "filename", request.id or "" )),
                 revision=str( result.get( "revision", "" )), detail=f"{ self.name }: " + ( "read back from the dashboard" if ok
                 else "mismatch: " + ", ".join( k for k, v in checks.items() if not v )))
        line = f"{ self.sentence( request, result )} Read back from the dashboard { yes( ok )}."
        details = { "notes": self.name, "verified": ok, **checks, **{ k: v for k, v in result.items() if k != "note" }}
        return done( line, details, body=json.dumps({ **result, "verified": ok })) if ok else failed( "read-back failed: " + line, details )


class CreateNote( Lifecycle ):
    name, allowed, required = "create", frozenset({ "category" }), frozenset({ "name", "text" })

    def shape_problem( self, request ):
        return None if request.text.strip() else "create text must not be blank"

    def perform( self, ctx, task, request ):
        return ctx.gateway.create( create_hint( request.name, request.category ), request.text, task.task_id )

    def verify( self, ctx, task, request, result ):
        served = ctx.endpoint.served( result[ "filename" ])
        return { "dashboard_has_it": bool( served and served[ 1 ] == result[ "revision" ] and served[ 0 ].strip() == request.text.strip()),
                 "listed_on_dashboard": result[ "filename" ] in ctx.endpoint.listed()}

    def sentence( self, request, result ):
        return f"Created { result[ 'filename' ]} (revision { result[ 'revision' ][ :12 ]})."


class RenameNote( Lifecycle ):
    """Rename, and/or move to another category (category 0 = none). The text is never touched."""
    name, allowed, required = "rename", frozenset({ "new_name", "category" }), frozenset({ "id", "expected_revision" })

    def shape_problem( self, request ):
        return None if ( request.new_name is not None or request.category is not None ) else "rename needs new_name or category"

    def perform( self, ctx, task, request ):
        new_filename = renamed( request.id, request.new_name, request.category )
        if new_filename == request.id:
            return { "filename": request.id, "revision": request.expected_revision, "changed": False }
        return { **ctx.gateway.rename( request.id, request.expected_revision, new_filename, task.task_id ), "changed": True }

    def verify( self, ctx, task, request, result ):
        new = ctx.endpoint.served( result[ "filename" ])
        old_gone = result[ "filename" ] == request.id or ctx.endpoint.served( request.id ) is None
        if not result.get( "changed", True ):
            return { "dashboard_has_it": bool( new and new[ 1 ] == request.expected_revision )}
        return { "dashboard_has_new_name": bool( new and new[ 1 ] == result[ "revision" ] and result[ "revision" ] == request.expected_revision ),
                 "old_name_gone": old_gone}

    def sentence( self, request, result ):
        return ( f"Renamed { request.id } to { result[ 'filename' ]}." if result.get( "changed", True )
                 else f"{ request.id } already has that name and category; nothing moved." )


class TrashNote( Lifecycle ):
    name, required = "trash", frozenset({ "id", "expected_revision" })

    def perform( self, ctx, task, request ):
        return ctx.gateway.trash( request.id, request.expected_revision, task.task_id )

    def verify( self, ctx, task, request, result ):
        return { "gone_from_dashboard": ctx.endpoint.served( request.id ) is None,
                 "not_listed": request.id not in ctx.endpoint.listed(),
                 "in_trash": any( t.get( "trash_id" ) == result[ "trash_id" ] for t in ctx.gateway.trash_list( task.task_id ))}

    def sentence( self, request, result ):
        return f"Moved { request.id } to the trash as { result[ 'trash_id' ]}; restore brings it back."


class RestoreNote( Lifecycle ):
    name, required = "restore", frozenset({ "trash_id" })

    def perform( self, ctx, task, request ):
        return ctx.gateway.restore( request.trash_id, task.task_id )

    def verify( self, ctx, task, request, result ):
        served = ctx.endpoint.served( result[ "filename" ])
        return { "back_on_dashboard": bool( served and served[ 1 ] == result[ "revision" ]),
                 "out_of_trash": all( t.get( "trash_id" ) != request.trash_id for t in ctx.gateway.trash_list( task.task_id ))}

    def sentence( self, request, result ):
        return f"Restored { result[ 'filename' ]} from the trash."


class PurgeNote( Lifecycle ):
    """Permanent. Only from the trash, and only with the exact sentence recording EG's authorization."""
    name, required = "purge", frozenset({ "trash_id", "authorization" })

    def perform( self, ctx, task, request ):
        return ctx.gateway.purge( request.trash_id, request.authorization, task.task_id )

    def verify( self, ctx, task, request, result ):
        return { "gone_from_trash": all( t.get( "trash_id" ) != request.trash_id for t in ctx.gateway.trash_list( task.task_id ))}

    def sentence( self, request, result ):
        return f"Permanently deleted { request.trash_id }."


class CommandRegistry:
    """Finds the Command for an operation name."""

    def __init__( self ) -> None:
        self._by_name: dict[ str, NotesCommand ] = {}

    def register( self, command: NotesCommand ) -> "CommandRegistry":
        self._by_name[ command.name ] = command
        return self

    def get( self, name: str ) -> NotesCommand | None:
        return self._by_name.get( name )

    def names( self ) -> list[ str ]:
        return sorted( self._by_name )


def default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    for command in ( ListNotes(), SearchNotes(), ReadNote(), ReadHighlights(), ListTrash(), RefreshNote(),
                     UpdateNote(), ChangeHighlights(), CreateNote(), RenameNote(), TrashNote(), RestoreNote(), PurgeNote()):
        registry.register( command )
    return registry

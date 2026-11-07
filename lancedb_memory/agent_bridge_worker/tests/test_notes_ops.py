"""Notes operations offline: the edit/highlight Strategies, then each Command against an in-memory dashboard.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_notes_ops
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.notes_agent import NotesAgent
from agent_bridge_worker.notes_ops.base import NoteChanged, NotesDenied, NotesRefusal
from agent_bridge_worker.notes_ops.edits import AppendText, FindReplace, LineReplace, SectionReplace
from agent_bridge_worker.notes_ops.highlights import SPAN, AddHighlight, ClearAllHighlights, ClearHighlight, RecolorHighlight
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID

A = "2026-11-05_04-alpha.md"
B = "2026-11-05_beta.md"


def sha( text: str ) -> str:
    return hashlib.sha256( text.encode()).hexdigest()


def refuses( strategy, text: str ) -> str:
    try:
        strategy.apply( text )
    except NotesRefusal as exc:
        return str( exc )
    raise AssertionError( "expected a refusal" )


# -- Strategies ------------------------------------------------------------------------------------

def test_find_replace_needs_exactly_one_match() -> None:
    assert FindReplace( "b", "X" ).apply( "a b c" ) == "a X c"
    assert "not in the note" in refuses( FindReplace( "z", "" ), "a b" )
    assert "occurs 2 times" in refuses( FindReplace( "a", "" ), "a a" )


def test_line_replace_is_one_based_and_bounded() -> None:
    assert LineReplace( 2, "NEW" ).apply( "one\ntwo\nthree\n" ) == "one\nNEW\nthree\n"
    assert "does not exist" in refuses( LineReplace( 4, "x" ), "one\ntwo\nthree\n" )
    assert "does not exist" in refuses( LineReplace( 3, "x" ), "one\ntwo" )


def test_append_adds_at_the_end_on_its_own_line() -> None:
    assert AppendText( "tail" ).apply( "one\ntwo\n" ) == "one\ntwo\ntail"
    assert AppendText( "tail" ).apply( "one" ) == "one\ntail"
    assert AppendText( "tail" ).apply( "" ) == "tail"


def test_section_replace_keeps_the_heading_and_stops_at_the_next_peer() -> None:
    text = "# T\n\n## A\n\nold a\n\n### sub\n\nold sub\n\n## B\n\nbee\n"
    out = SectionReplace( "A", "fresh" ).apply( text )
    assert out == "# T\n\n## A\n\nfresh\n\n## B\n\nbee\n"
    last = SectionReplace( "B", "end" ).apply( text )
    assert last.endswith( "## B\n\nend\n" ) and "old a" in last
    assert "is not in the note" in refuses( SectionReplace( "Z", "" ), text )
    assert "occurs 2 times" in refuses( SectionReplace( "A", "" ), "## A\nx\n## A\ny\n" )


def test_highlight_strategies() -> None:
    marked = AddHighlight( "Prince Street", "yellow" ).apply( "at Prince Street now" )
    assert marked == "at {{highlight:yellow}}Prince Street{{/highlight}} now"
    assert RecolorHighlight( "Prince Street", "red" ).apply( marked ) == "at {{highlight:red}}Prince Street{{/highlight}} now"
    assert ClearHighlight( "Prince Street" ).apply( marked ) == "at Prince Street now"
    two = "{{highlight:red}}a{{/highlight}} and {{highlight:blue}}b{{/highlight}}"
    assert ClearAllHighlights().apply( two ) == "a and b"
    assert "overlaps" in refuses( AddHighlight( "Prince", "red" ), marked )
    assert "is not highlighted" in refuses( ClearHighlight( "nope" ), marked )
    assert "one line" in refuses( AddHighlight( "a\nb", "red" ), "a\nb" )
    assert "no highlights" in refuses( ClearAllHighlights(), "plain" )


# -- an in-memory dashboard ----------------------------------------------------------------------------

class Dashboard:
    """Gateway + endpoint over a dict, with the dashboard's rules (revisions, trash, purge phrase)."""

    def __init__( self ) -> None:
        self.notes: dict[ str, str ] = { A: "# Alpha\n\nline one\n", B: "beta\n" }
        self.trash_bin: dict[ str, str ] = {}
        self.writes = 0
        self.deny_all = False

    # INotesBridge
    def _gate( self ) -> None:
        if self.deny_all:
            raise NotesDenied( "note_protected", 403 )

    def list( self, task_id ):
        return sorted( self.notes, reverse=True )

    def read( self, filename, task_id ):
        if filename not in self.notes:
            raise FileNotFoundError( filename )
        return self.notes[ filename ], sha( self.notes[ filename ])

    def write( self, filename, text, expected_revision, task_id ):
        self._gate()
        if sha( self.notes[ filename ]) != expected_revision:
            raise NoteChanged( "it changed" )
        self.notes[ filename ] = text
        self.writes += 1
        return sha( text )

    # INotesCatalog
    def entries( self, query, category, task_id ):
        out = []
        for name, text in sorted( self.notes.items(), reverse=True ):
            if query and query.lower() not in name.lower() and query.lower() not in text.lower():
                continue
            if category is not None and f"_{ category:02d}-" not in name:
                continue
            out.append({ "filename": name, "revision": sha( text )})
        return out

    def highlights( self, filename, task_id ):
        text = self.notes[ filename ]
        return { "revision": sha( text ), "review_marks": [],
                 "highlights": [{ "color": m.group( 1 ), "text": m.group( 2 )} for m in SPAN.finditer( text )]}

    def trash_list( self, task_id ):
        return [{ "trash_id": t, "filename": t.split( "_", 1 )[ 1 ]} for t in self.trash_bin]

    # INotesLifecycle
    def create( self, hint, text, task_id ):
        filename = f"2026-11-07_{ hint }.md"
        self.notes[ filename ] = text if text.endswith( "\n" ) else text + "\n"
        return { "filename": filename, "revision": sha( self.notes[ filename ])}

    def rename( self, filename, expected_revision, new_filename, task_id ):
        self._gate()
        if sha( self.notes[ filename ]) != expected_revision:
            raise NoteChanged( "it changed" )
        self.notes[ new_filename ] = self.notes.pop( filename )
        return { "filename": new_filename, "revision": sha( self.notes[ new_filename ])}

    def trash( self, filename, expected_revision, task_id ):
        self._gate()
        if sha( self.notes[ filename ]) != expected_revision:
            raise NoteChanged( "it changed" )
        trash_id = f"20261107120000_{ filename }"
        self.trash_bin[ trash_id ] = self.notes.pop( filename )
        return { "trash_id": trash_id, "filename": filename, "revision": expected_revision }

    def restore( self, trash_id, task_id ):
        filename = trash_id.split( "_", 1 )[ 1 ]
        self.notes[ filename ] = self.trash_bin.pop( trash_id )
        return { "filename": filename, "revision": sha( self.notes[ filename ])}

    def purge( self, trash_id, authorization, task_id ):
        if authorization != f"EG authorizes permanently deleting { trash_id }":
            raise NotesDenied( "authorization_required", 403 )
        self.trash_bin.pop( trash_id )
        return { "trash_id": trash_id, "purged": True }

    # INotesEndpoint (what the browser sees)
    def served( self, filename ):
        return ( self.notes[ filename ], sha( self.notes[ filename ])) if filename in self.notes else None

    def listed( self ):
        return sorted( self.notes )


class NoStore:
    def names( self ): return []
    def read( self, f ): return None


def ask( dash: Dashboard, payload: dict, operation: str | None = None ):
    op = operation or ( "inspect" if payload[ "notes" ] in ( "list", "search", "read", "highlights", "trash_list", "refresh" )
                        else "implement" )
    t = ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent="notes",
                     workspace="notes", operation=op, approval_mode="read_only" if op == "inspect" else "workspace_write",
                     prompt=json.dumps( payload ))
    return NotesAgent( NoStore(), dash, dash ).run( t, Path( "/nonexistent" ), lambda: "continue" )


def rev( dash: Dashboard, name: str ) -> str:
    return sha( dash.notes[ name ])


# -- Commands ----------------------------------------------------------------------------------------

def test_list_search_and_category() -> None:
    d = Dashboard()
    assert json.loads( ask( d, { "notes": "list" }).body )[ "approved" ] == [ B, A ]
    found = json.loads( ask( d, { "notes": "search", "query": "LINE ONE" }).body )
    assert found[ "approved" ] == [ A ]
    assert json.loads( ask( d, { "notes": "list", "category": 4 }).body )[ "approved" ] == [ A ]
    assert ask( d, { "notes": "search" }).kind is OutcomeKind.REFUSED


def test_update_by_line_and_section_is_verified() -> None:
    d = Dashboard()
    out = ask( d, { "notes": "update", "id": A, "expected_revision": rev( d, A ),
                    "edits": [{ "line": 3, "replace": "line ONE" }, { "section": "Alpha", "replace": "new body" }]})
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "verified" ], out.summary
    assert d.notes[ A ] == "# Alpha\n\nnew body\n"
    bad = ask( d, { "notes": "update", "id": A, "expected_revision": rev( d, A ), "edits": [{ "line": 99, "replace": "x" }]})
    assert bad.kind is OutcomeKind.REFUSED and "edit 1" in bad.summary and d.notes[ A ] == "# Alpha\n\nnew body\n"


def test_an_edit_must_name_exactly_one_locator() -> None:
    d = Dashboard()
    out = ask( d, { "notes": "update", "id": A, "expected_revision": rev( d, A ),
                    "edits": [{ "find": "x", "line": 1, "replace": "y" }]})
    assert out.kind is OutcomeKind.REFUSED and d.writes == 0


def test_highlight_add_recolor_clear_with_dashboard_agreement() -> None:
    d = Dashboard()
    out = ask( d, { "notes": "highlight", "id": A, "expected_revision": rev( d, A ),
                    "marks": [{ "action": "add", "text": "line one", "color": "yellow" }]})
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "dashboard_highlights_match" ], out.summary
    assert "{{highlight:yellow}}line one{{/highlight}}" in d.notes[ A ]
    out = ask( d, { "notes": "highlight", "id": A, "expected_revision": rev( d, A ),
                    "marks": [{ "action": "recolor", "text": "line one", "color": "red" }]})
    assert out.kind is OutcomeKind.SUCCEEDED and "{{highlight:red}}" in d.notes[ A ]
    out = ask( d, { "notes": "highlight", "id": A, "expected_revision": rev( d, A ), "marks": [{ "action": "clear", "all": True }]})
    assert out.kind is OutcomeKind.SUCCEEDED and "{{" not in d.notes[ A ]
    body = json.loads( ask( d, { "notes": "highlights", "id": A }).body )
    assert body[ "highlights" ] == [] and "review_marks" in body


def test_highlight_that_the_dashboard_would_not_render_is_not_reported_as_done() -> None:
    d = Dashboard()
    real = d.highlights
    d.highlights = lambda f, t: { **real( f, t ), "highlights": []}      # a palette the dashboard does not know
    out = ask( d, { "notes": "highlight", "id": A, "expected_revision": rev( d, A ),
                    "marks": [{ "action": "add", "text": "line one", "color": "mauve" }]})
    assert out.kind is OutcomeKind.FAILED and "read-back failed" in out.summary


def test_refresh_reports_an_external_change() -> None:
    d = Dashboard()
    old = rev( d, B )
    same = json.loads( ask( d, { "notes": "refresh", "id": B, "known_revision": old }).body )
    assert same[ "changed" ] is False and "note" not in same
    d.notes[ B ] = "edited elsewhere\n"
    now = json.loads( ask( d, { "notes": "refresh", "id": B, "known_revision": old }).body )
    assert now[ "changed" ] and now[ "note" ] == "edited elsewhere\n" and now[ "revision" ] == rev( d, B )


def test_create_rename_trash_restore_purge() -> None:
    d = Dashboard()
    out = ask( d, { "notes": "create", "name": "Gamma Note", "text": "gamma", "category": 2 })
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "verified" ], out.summary
    name = json.loads( out.body )[ "filename" ]
    assert name.endswith( "_02-gamma-note.md" )
    out = ask( d, { "notes": "rename", "id": name, "expected_revision": rev( d, name ), "category": 4, "new_name": "Gamma Final" })
    assert out.kind is OutcomeKind.SUCCEEDED and name not in d.notes, out.summary
    moved = json.loads( out.body )[ "filename" ]
    out = ask( d, { "notes": "trash", "id": moved, "expected_revision": rev( d, moved )})
    assert out.kind is OutcomeKind.SUCCEEDED and moved not in d.notes and len( d.trash_bin ) == 1
    trash_id = json.loads( out.body )[ "trash_id" ]
    assert json.loads( ask( d, { "notes": "trash_list" }).body )[ "trash" ][ 0 ][ "trash_id" ] == trash_id
    assert ask( d, { "notes": "restore", "trash_id": trash_id }).kind is OutcomeKind.SUCCEEDED and moved in d.notes
    again = json.loads( ask( d, { "notes": "trash", "id": moved, "expected_revision": rev( d, moved )}).body )[ "trash_id" ]
    wrong = ask( d, { "notes": "purge", "trash_id": again, "authorization": "go ahead" })
    assert wrong.kind is OutcomeKind.REFUSED and again in d.trash_bin
    phrase = f"EG authorizes permanently deleting { again }"
    assert ask( d, { "notes": "purge", "trash_id": again, "authorization": phrase }).kind is OutcomeKind.SUCCEEDED
    assert d.trash_bin == {}


def test_purge_cannot_name_a_live_note() -> None:
    d = Dashboard()
    out = ask( d, { "notes": "purge", "trash_id": A, "authorization": "x" })
    assert out.kind is OutcomeKind.REFUSED and A in d.notes


def test_stale_revisions_change_nothing() -> None:
    d = Dashboard()
    stale = "0" * 64
    for payload in ({ "notes": "update", "id": A, "expected_revision": stale, "text": "x" },
                    { "notes": "highlight", "id": A, "expected_revision": stale, "marks": [{ "action": "clear", "all": True }]},
                    { "notes": "rename", "id": A, "expected_revision": stale, "new_name": "n" },
                    { "notes": "trash", "id": A, "expected_revision": stale }):
        out = ask( d, payload )
        assert out.kind is OutcomeKind.FAILED and out.details[ "conflict" ], payload
    assert A in d.notes and d.trash_bin == {} and d.writes == 0


def test_a_protected_note_refusal_surfaces_as_refused() -> None:
    d = Dashboard()
    d.deny_all = True
    for payload in ({ "notes": "update", "id": A, "expected_revision": rev( d, A ), "text": "x" },
                    { "notes": "trash", "id": A, "expected_revision": rev( d, A )}):
        assert ask( d, payload ).kind is OutcomeKind.REFUSED, payload
    assert d.notes[ A ].startswith( "# Alpha" )


def test_file_names_for_create_rename_and_move() -> None:
    from agent_bridge_worker.notes_ops.names import create_hint, original_of, renamed
    assert create_hint( "Probate Deed!", 4 ) == "04-probate-deed" and create_hint( "plain", None ) == "plain"
    assert renamed( "2026-11-05_04-deed.md", None, 5 ) == "2026-11-05_05-deed.md"          # move
    assert renamed( "2026-11-05_04-deed.md", "Final Deed", None ) == "2026-11-05_04-final-deed.md"
    assert renamed( "2026-11-05_04-deed.md", None, 0 ) == "2026-11-05_deed.md"             # out of its category
    assert renamed( "2026-11-05_deed.md", "x", 2 ) == "2026-11-05_02-x.md"
    assert original_of( "20261107120000_2026-11-05_x.md" ) == "2026-11-05_x.md"
    assert original_of( "20261107120000_2_2026-11-05_x.md" ) == "2026-11-05_x.md"
    for bad in (( "notes.md", "x", None ), ( "2026-11-05_a.md", "!!!", None ), ( "2026-11-05_a.md", None, 11 )):
        try:
            renamed( *bad )
            raise AssertionError( bad )
        except NotesRefusal:
            pass
    try:
        create_hint( "", None )
        raise AssertionError( "empty name" )
    except NotesRefusal:
        pass


def test_a_rename_to_the_same_name_changes_nothing() -> None:
    d = Dashboard()
    out = ask( d, { "notes": "rename", "id": A, "expected_revision": rev( d, A ), "category": 4 })     # A is already 04-
    assert out.kind is OutcomeKind.SUCCEEDED and "nothing moved" in out.summary and A in d.notes


def test_every_operation_has_one_command_and_the_right_lane() -> None:
    d = Dashboard()
    for payload, wrong in (({ "notes": "trash_list" }, "implement" ), ({ "notes": "create", "name": "n", "text": "t" }, "inspect" ),
                           ({ "notes": "purge", "trash_id": "20261107120000_x.md", "authorization": "a" }, "inspect" )):
        assert ask( d, payload, wrong ).kind is OutcomeKind.REFUSED, payload


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )

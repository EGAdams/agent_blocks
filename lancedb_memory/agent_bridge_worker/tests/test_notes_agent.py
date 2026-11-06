"""NotesAgent and its filesystem store, offline. A temporary directory stands in for ~/notes.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_notes_agent
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.notes_store import FilesystemNotesStore, HttpNotesEndpoint
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.adapters.notes_activity_log import JsonlNotesActivity
from agent_bridge_worker.adapters.notes_bridge_http import HttpNotesBridge
from agent_bridge_worker.notes_agent import HEADER, NoteChanged, NotesAgent, NotesDenied
from agent_bridge_worker.policy import FixedFirstWorkspaces, ScopedPolicy, StaticWorkspaces
from agent_bridge_worker.service import BridgeTaskRun
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID, FakeGateway
from agent_comms.tests.fakes import FakeClock

MARKER = "NOTESDIAG-0123456789ab"
DEED   = "2026-11-03_draft-personal-representative-s-deed-of-distribution.md"
PRIVATE = "Grantee: private words that must never come back"
CAPS   = { "notes": { "inspect": False, "implement": True }}
MODES  = frozenset({ "read_only", "workspace_write" })
TODAY  = lambda: datetime.date( 2026, 11, 5 )


def task( notes: str = "marker_write", marker: str | None = MARKER, operation: str | None = None, prompt: str | None = None ) -> ClaimedTask:
    body = prompt if prompt is not None else json.dumps({ "notes": notes, **({ "marker": marker } if marker else {})})
    op   = operation or ( "inspect" if notes == "status" else "implement" )
    return ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1,
                        agent="notes", workspace="notes", operation=op,
                        approval_mode="read_only" if op == "inspect" else "workspace_write", prompt=body )


class StoreEndpoint:
    """INotesEndpoint served straight from a store, as the dashboard would; `stale` serves an old copy."""

    def __init__( self, store: FilesystemNotesStore ) -> None:
        self.store, self.stale = store, None

    def served( self, filename: str ):
        return self.stale if self.stale is not None else self.store.read( filename )

    def listed( self ) -> list[ str ]:
        return self.store.names()


def notes_dir() -> tuple[ Path, FilesystemNotesStore, StoreEndpoint ]:
    root = Path( tempfile.mkdtemp())
    ( root / DEED ).write_text( f"# Deed\n\n{ PRIVATE }\n", encoding="utf-8" )
    store = FilesystemNotesStore( root, TODAY )
    return root, store, StoreEndpoint( store )


def go() -> str:
    return "continue"


def run( store, endpoint, t: ClaimedTask, keep_alive=go ):
    return NotesAgent( store, endpoint ).run( t, Path( "/nonexistent" ), keep_alive )


def everything( out ) -> str:
    return out.summary + out.body + json.dumps( out.details or {})


# -- the round trip -------------------------------------------------------------------

def test_a_marker_write_creates_the_diagnostic_note_and_reads_it_back_from_both_boundaries() -> None:
    root, store, endpoint = notes_dir()
    deed_before = ( root / DEED ).read_bytes()
    out = run( store, endpoint, task())
    assert out.kind is OutcomeKind.SUCCEEDED, out.summary
    d = out.details
    assert d[ "filename" ] == "2026-11-05_bridge-notes-diagnostic.md" and d[ "created" ] is True
    assert d[ "read_back_ok" ] and d[ "store_marker" ] and d[ "served_marker" ] and d[ "revisions_agree" ] and d[ "listed" ]
    assert ( root / d[ "filename" ]).read_text().startswith( HEADER )
    assert ( root / DEED ).read_bytes() == deed_before                    # the deed is never touched
    assert PRIVATE not in everything( out ) and "Grantee" not in everything( out )


def test_a_second_write_updates_the_same_note_with_its_revision() -> None:
    root, store, endpoint = notes_dir()
    first  = run( store, endpoint, task())
    second = run( store, endpoint, task( marker="NOTESDIAG-ffffffffffff" ))
    assert second.kind is OutcomeKind.SUCCEEDED and second.details[ "created" ] is False
    assert second.details[ "filename" ] == first.details[ "filename" ]
    assert second.details[ "revision_before" ] == first.details[ "revision_after" ]
    assert second.details[ "revision_after" ] != first.details[ "revision_after" ]
    assert len([ n for n in store.names() if "diagnostic" in n ]) == 1


def test_a_stale_served_copy_is_a_failed_read_back_not_a_success() -> None:
    root, store, endpoint = notes_dir()
    endpoint.stale = ( f"{ HEADER }\n\nMarker: NOTESDIAG-000000000000\n", "old-revision" )
    out = run( store, endpoint, task())
    assert out.kind is OutcomeKind.FAILED and out.details[ "read_back_ok" ] is False
    assert out.details[ "store_marker" ] is True and out.details[ "served_marker" ] is False
    assert "read-back failed" in out.summary


def test_status_reports_each_boundary_without_any_note_text() -> None:
    root, store, endpoint = notes_dir()
    run( store, endpoint, task())
    out = run( store, endpoint, task( "status" ))
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "notes_total" ] == 2
    seen = out.details[ "diagnostic" ][ 0 ]
    assert seen[ "store_marker" ] and seen[ "served_marker" ] and seen[ "revisions_agree" ]
    assert "agree" in out.summary and PRIVATE not in everything( out )


def test_clear_removes_only_the_diagnostic_note() -> None:
    root, store, endpoint = notes_dir()
    run( store, endpoint, task())
    out = run( store, endpoint, task( "marker_clear", marker=None ))
    assert out.kind is OutcomeKind.SUCCEEDED, out.summary
    assert [ r[ "gone_from_store" ] and r[ "gone_from_endpoint" ] for r in out.details[ "removed" ]] == [ True ]
    assert store.names() == [ DEED ]
    again = run( store, endpoint, task( "marker_clear", marker=None ))
    assert again.kind is OutcomeKind.SUCCEEDED and "no diagnostic note" in again.summary


# -- what it will not touch ---------------------------------------------------------------

def test_a_note_named_like_ours_without_the_header_is_never_written_or_deleted() -> None:
    root, store, endpoint = notes_dir()
    impostor = root / "2026-11-05_bridge-notes-diagnostic.md"
    impostor.write_text( "# Somebody's real note\n", encoding="utf-8" )
    out = run( store, endpoint, task())
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "filename" ] == "2026-11-05_bridge-notes-diagnostic-2.md"
    run( store, endpoint, task( "marker_clear", marker=None ))
    assert impostor.read_text() == "# Somebody's real note\n"


def test_free_text_and_mismatched_operations_are_refused() -> None:
    root, store, endpoint = notes_dir()
    for t in ( task( prompt="please update the deed note to say Prince Street" ),
               task( prompt=json.dumps({ "notes": "marker_write", "marker": "x; rm -rf ~" })),
               task( prompt=json.dumps({ "notes": "marker_write", "filename": DEED, "marker": MARKER })),
               task( "marker_write", marker=None ),
               task( "marker_write", operation="inspect" ),
               task( "status", operation="implement" )):
        out = run( store, endpoint, t )
        assert out.kind is OutcomeKind.REFUSED, t.prompt
    assert store.names() == [ DEED ]


def test_a_cancel_before_it_starts_writes_nothing() -> None:
    root, store, endpoint = notes_dir()
    out = run( store, endpoint, task(), keep_alive=lambda: "cancel" )
    assert out.kind is OutcomeKind.CANCELLED and store.names() == [ DEED ]


# -- the store keeps the dashboard's rules ----------------------------------------------------

def test_the_revision_is_the_dashboards_sha256_of_the_file_bytes() -> None:
    root, store, _ = notes_dir()
    assert store.read( DEED )[ 1 ] == hashlib.sha256(( root / DEED ).read_bytes()).hexdigest()


def test_a_write_at_an_old_revision_is_refused_and_changes_nothing() -> None:
    root, store, endpoint = notes_dir()
    name, rev = store.create( "bridge-notes-diagnostic", f"{ HEADER }\n" )
    ( root / name ).write_text( f"{ HEADER }\n\nedited by hand\n", encoding="utf-8" )
    try:
        store.write( name, f"{ HEADER }\nnew\n", rev )
    except Exception as exc:                    # noqa: BLE001
        assert type( exc ).__name__ == "NoteChanged"
    else:
        raise AssertionError( "a stale revision was accepted" )
    assert "edited by hand" in ( root / name ).read_text()


def test_paths_outside_the_store_are_refused() -> None:
    _, store, _ = notes_dir()
    for bad in ( "../x.md", "/etc/passwd", ".hidden.md", "notes.txt" ):
        try:
            store.read( bad )
        except ValueError:
            continue
        raise AssertionError( bad )


def test_the_endpoint_is_loopback_only() -> None:
    try:
        HttpNotesEndpoint( "http://100.102.209.100:8765" )
    except ValueError:
        return
    raise AssertionError( "a non-loopback endpoint was accepted" )


# -- through the worker -------------------------------------------------------------------------

def test_a_notes_write_runs_through_the_worker_without_a_git_write_session() -> None:
    root, store, endpoint = notes_dir()
    gateway = FakeGateway()
    gateway._claim.update({ "agent": "notes", "workspace": "notes", "operation": "implement",
                            "approval_mode": "workspace_write", "prompt": json.dumps({ "notes": "marker_write", "marker": MARKER })})
    agent    = NotesAgent( store, endpoint )
    resolver = FixedFirstWorkspaces({ "notes": Path( tempfile.mkdtemp())}, StaticWorkspaces({}))
    BridgeTaskRun( gateway, ScopedPolicy( CAPS, frozenset({ "notes" }), MODES ), resolver, { "notes": agent }, agent,
                   FakeClock(), "worker-test", self_contained=frozenset({ "notes" })).run_one()
    assert gateway.completed[ "ok" ] is True, gateway.completed
    assert gateway.completed[ "details" ][ "read_back_ok" ] is True
    assert "changed_paths" not in gateway.completed[ "details" ]


# -- approved notes: list, read, update through the Notes interface ---------------------------

class FakeBridge:
    """INotesBridge as the dashboard's notes_bridge.py behaves: approved notes only, revision-checked writes."""

    def __init__( self, store: FilesystemNotesStore, approved=( DEED, ), deny: str | None = None ) -> None:
        self.store, self.approved, self.deny, self.writes, self.before_write = store, set( approved ), deny, 0, None

    def _gate( self, filename: str | None, scope: str ) -> None:
        if self.deny == scope:
            raise NotesDenied( "insufficient_scope", 403 )
        if filename is not None and filename not in self.approved:
            raise NotesDenied( "note_not_approved", 403 )

    def list( self, task_id: str ) -> list[ str ]:
        self._gate( None, "notes:read" )
        return [ n for n in self.store.names() if n in self.approved ]

    def read( self, filename: str, task_id: str ):
        self._gate( filename, "notes:read" )
        got = self.store.read( filename )
        if got is None:
            raise FileNotFoundError( filename )
        return got

    def write( self, filename: str, text: str, expected_revision: str, task_id: str ) -> str:
        self._gate( filename, "notes:write" )
        if self.before_write is not None:
            self.before_write()
        self.writes += 1
        return self.store.write( filename, text, expected_revision )


class Activity:
    def __init__( self ) -> None:
        self.events: list[ dict ] = []

    def record( self, event: str, **fields: str ) -> None:
        self.events.append({ "event": event, **fields })


def approved( **kwargs ):
    root, store, endpoint = notes_dir()
    bridge, log = FakeBridge( store, **kwargs ), Activity()
    return root, store, endpoint, bridge, log, NotesAgent( store, endpoint, bridge, log )


def ask( agent, payload: dict ):
    op = "inspect" if payload[ "notes" ] in ( "list", "read" ) else "implement"
    return agent.run( task( prompt=json.dumps( payload ), operation=op ), Path( "/nonexistent" ), go )


def rev_of( root: Path ) -> str:
    return hashlib.sha256(( root / DEED ).read_bytes()).hexdigest()


def test_list_and_read_return_the_approved_note_and_its_revision() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    ( root / "2026-11-04_other.md" ).write_text( "not approved\n" )
    out = ask( agent, { "notes": "list" })
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "approved" ] == [ DEED ]
    out = ask( agent, { "notes": "read", "id": DEED })
    assert out.kind is OutcomeKind.SUCCEEDED
    assert json.loads( out.body ) == { "id": DEED, "revision": rev_of( root ), "note": f"# Deed\n\n{ PRIVATE }\n" }


def test_an_edit_changes_only_its_text_and_is_verified_at_the_persisted_revision() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    ( root / DEED ).write_text( "# Deed\n\nconveys to A, [STATUS], whose address is B\n\nLegal description: Lot 9\n" )
    before = rev_of( root )
    out = ask( agent, { "notes": "update", "id": DEED, "expected_revision": before,
                        "edits": [{ "find": ", [STATUS]", "replace": "" }]})
    assert out.kind is OutcomeKind.SUCCEEDED, out.summary
    assert ( root / DEED ).read_text() == "# Deed\n\nconveys to A, whose address is B\n\nLegal description: Lot 9\n"
    d = out.details
    assert d[ "revision_before" ] == before and d[ "revision_after" ] == rev_of( root ) != before
    assert d[ "verified" ] and d[ "dashboard_revision_matches" ] and d[ "interface_text_matches" ]
    assert rev_of( root )[ :12 ] in out.summary
    assert "-conveys to A, [STATUS]" in json.loads( out.body )[ "diff" ]
    assert log.events[ -1 ][ "event" ] == "verified" and log.events[ -1 ][ "revision" ] == rev_of( root )
    assert "Lot 9" not in json.dumps( log.events )


def test_a_stale_revision_is_a_conflict_and_nothing_is_written() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    stale = rev_of( root )
    ( root / DEED ).write_text( "# Deed\n\nEG edited this in the browser\n" )
    out = ask( agent, { "notes": "update", "id": DEED, "expected_revision": stale, "text": "# Deed\n\nmine\n" })
    assert out.kind is OutcomeKind.FAILED and out.details[ "conflict" ] and not out.retryable
    assert out.details[ "current_revision" ] == rev_of( root )
    assert "EG edited this" in ( root / DEED ).read_text() and bridge.writes == 0
    assert log.events[ -1 ][ "event" ] == "conflict"


def test_an_edit_racing_a_browser_save_is_a_conflict_not_an_overwrite() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    bridge.before_write = lambda: ( root / DEED ).write_text( "# Deed\n\nsaved in the browser meanwhile\n" )
    out = ask( agent, { "notes": "update", "id": DEED, "expected_revision": rev_of( root ), "text": "# Deed\n\nmine\n" })
    assert out.kind is OutcomeKind.FAILED and out.details[ "conflict" ], out.summary
    assert "meanwhile" in ( root / DEED ).read_text()


def test_an_ambiguous_or_missing_find_writes_nothing() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    ( root / DEED ).write_text( "# Deed\n\nX and X\n" )
    for find in ( "X", "Y" ):
        out = ask( agent, { "notes": "update", "id": DEED, "expected_revision": rev_of( root ),
                            "edits": [{ "find": find, "replace": "Z" }]})
        assert out.kind is OutcomeKind.REFUSED, find
    assert ( root / DEED ).read_text() == "# Deed\n\nX and X\n" and bridge.writes == 0


def test_permission_failures_are_refused_and_logged() -> None:
    for kwargs, payload in (( { "deny": "notes:write" }, { "notes": "update", "edits": [{ "find": "Deed", "replace": "D" }]}),
                            ( { "approved": ()}, { "notes": "read" }),
                            ( { "deny": "notes:read" }, { "notes": "read" })):
        root, store, endpoint, bridge, log, agent = approved( **kwargs )
        payload = { **payload, "id": DEED }
        if payload[ "notes" ] == "update":
            payload[ "expected_revision" ] = rev_of( root )
        before = ( root / DEED ).read_bytes()
        out = ask( agent, payload )
        assert out.kind is OutcomeKind.REFUSED, ( kwargs, out.summary )
        assert ( root / DEED ).read_bytes() == before
        assert log.events[ -1 ][ "event" ] == "auth_failed"


def test_without_a_provisioned_token_approved_note_work_is_refused() -> None:
    root, store, endpoint = notes_dir()
    out = NotesAgent( store, endpoint ).run( task( prompt=json.dumps({ "notes": "list" }), operation="inspect" ),
                                             Path( "/nonexistent" ), go )
    assert out.kind is OutcomeKind.REFUSED and "not provisioned" in out.summary


def test_a_dashboard_serving_an_old_copy_fails_verification() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    endpoint.stale = store.read( DEED )
    out = ask( agent, { "notes": "update", "id": DEED, "expected_revision": rev_of( root ), "text": "# Deed\n\nnew\n" })
    assert out.kind is OutcomeKind.FAILED and out.details[ "verified" ] is False
    assert log.events[ -1 ][ "event" ] == "verify_failed"


def test_malformed_update_requests_are_refused() -> None:
    root, store, endpoint, bridge, log, agent = approved()
    rev = rev_of( root )
    for payload in ({ "notes": "update", "id": DEED, "text": "x" },                                   # no revision
                    { "notes": "update", "id": DEED, "expected_revision": rev },                      # nothing to do
                    { "notes": "update", "id": DEED, "expected_revision": rev, "text": "x",
                      "edits": [{ "find": "a", "replace": "b" }]},                                    # both
                    { "notes": "update", "id": "../etc/passwd", "expected_revision": rev, "text": "x" },
                    { "notes": "update", "id": DEED, "expected_revision": rev, "text": "   " },
                    { "notes": "read" },
                    { "notes": "list", "id": DEED },
                    { "notes": "marker_write", "marker": MARKER, "id": DEED }):
        out = ask( agent, payload ) if payload[ "notes" ] != "marker_write" else agent.run(
            task( prompt=json.dumps( payload )), Path( "/nonexistent" ), go )
        assert out.kind is OutcomeKind.REFUSED, payload
    assert bridge.writes == 0


def test_the_http_bridge_sends_the_task_and_maps_answers() -> None:
    import http.server
    import threading
    seen: list[ dict ] = []

    class Handler( http.server.BaseHTTPRequestHandler ):
        def log_message( self, *a ) -> None:
            pass

        def _answer( self, code: int, payload: dict ) -> None:
            raw = json.dumps( payload ).encode()
            self.send_response( code )
            self.send_header( "Content-Length", str( len( raw )))
            self.end_headers()
            self.wfile.write( raw )

        def do_GET( self ) -> None:
            seen.append({ "path": self.path, "task": self.headers.get( "X-Bridge-Task" ),
                          "auth": self.headers.get( "Authorization" )})
            if self.path.endswith( "/secret.md" ):
                return self._answer( 403, { "error": "note_not_approved" })
            self._answer( 200, { "note": "n\n", "revision": "r1" })

        def do_POST( self ) -> None:
            body = json.loads( self.rfile.read( int( self.headers[ "Content-Length" ])))
            if body[ "expected_revision" ] != "r1":
                return self._answer( 409, { "error": "note_changed", "revision": "r1" })
            self._answer( 200, { "revision": "r2" })

    server = http.server.ThreadingHTTPServer(( "127.0.0.1", 0 ), Handler )
    threading.Thread( target=server.serve_forever, daemon=True ).start()
    try:
        bridge = HttpNotesBridge( f"http://127.0.0.1:{ server.server_address[ 1 ]}", "x" * 32 )
        assert bridge.read( DEED, "t_0123456789abcdef" ) == ( "n\n", "r1" )
        assert seen[ -1 ][ "task" ] == "t_0123456789abcdef" and seen[ -1 ][ "path" ].endswith( f"/notes/{ DEED }" )
        assert bridge.write( DEED, "m\n", "r1", "t_0123456789abcdef" ) == "r2"
        for call, exc in (( lambda: bridge.write( DEED, "m\n", "old", "" ), NoteChanged ),
                          ( lambda: bridge.read( "secret.md", "" ), NotesDenied )):
            try:
                call()
            except exc:
                continue
            raise AssertionError( exc )
        assert "x" * 32 not in repr( bridge )
    finally:
        server.shutdown()
    try:
        HttpNotesBridge( "http://100.102.209.100:8765", "x" * 32 )
    except ValueError:
        return
    raise AssertionError( "a non-loopback Notes interface was accepted" )


def test_the_activity_log_is_jsonl_without_note_text() -> None:
    path = Path( tempfile.mkdtemp()) / "a" / "notes_activity.jsonl"
    JsonlNotesActivity( path ).record( "verified", task_id="t_01234567", filename=DEED, revision="r2", note=PRIVATE )
    [ line ] = path.read_text().splitlines()
    entry = json.loads( line )
    assert entry[ "source" ] == "worker" and entry[ "event" ] == "verified" and entry[ "task_id" ] == "t_01234567"
    assert PRIVATE not in line and path.stat().st_mode & 0o077 == 0


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )

"""Offline tests for object_trace. No network, no API key; every store is a temp file.

    cd lancedb_memory && .venv/bin/python object_trace/tests/test_object_trace.py
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from object_trace.adapters.sqlite_store import SqliteTraceStore, migrate, version_of           # noqa: E402
from object_trace.adapters.sssf_reader import SssfReader                                       # noqa: E402
from object_trace.adw_hooks import AdwRunTrace                                                 # noqa: E402
from object_trace.inspector import Inspector                                                   # noqa: E402
from object_trace.liveness import view                                                         # noqa: E402
from object_trace.models import TraceEvent, TraceObject                                        # noqa: E402
from object_trace.redact import BoundedRedactor                                                # noqa: E402
from object_trace.service import InspectionApp                                                 # noqa: E402
from object_trace.tracer import NullSink, ObjectTracer, SafeSink, trace_scope                  # noqa: E402


class Clock:
    def __init__( self, t: float = 1_800_000_000.0 ) -> None:
        self.t = t

    def now( self ) -> float:
        return self.t


def _store() -> tuple[ SqliteTraceStore, Path ]:
    d = Path( tempfile.mkdtemp( prefix="otrace-" ))
    return SqliteTraceStore( d / "t.db" ), d / "t.db"


def _tracer( store, clock: Clock ) -> ObjectTracer:
    return ObjectTracer( store, clock=clock, host="testhost", pid=4242, process_session="sess1" )


NO_PID = lambda host, pid: None


# -- redaction --------------------------------------------------------------------------------

def test_redaction_masks_secrets_hashes_private_text_and_keeps_counts() -> None:
    r = BoundedRedactor()
    out = r.clean({ "lease_token": "abc", "Authorization": "x", "password": "p", "input_tokens": 12, "lease_seconds": 90,
                    "prompt": "my private note", "note": "dear diary", "summary": "used Bearer abcdefghijkl123 here",
                    "nested": { "api_key": "k", "x": [ "sk-ant-abcdefghijklmnop" ]}})
    assert out[ "lease_token" ] == "[redacted]" and out[ "Authorization" ] == "[redacted]" and out[ "password" ] == "[redacted]"
    assert out[ "input_tokens" ] == 12 and out[ "lease_seconds" ] == 90
    assert set( out[ "prompt" ]) == { "sha256", "bytes" } and "diary" not in json.dumps( out )
    assert "abcdefghijkl123" not in out[ "summary" ]
    assert out[ "nested" ][ "api_key" ] == "[redacted]" and "abcdefghijklmnop" not in json.dumps( out )


def test_redaction_bounds_size() -> None:
    out = BoundedRedactor().clean({ f"k{ i }": "x" * 300 for i in range( 60 )})
    assert len( json.dumps( out )) <= 6000


# -- store: dedup, migration, retention, correlation ----------------------------------------------

def test_events_dedup_by_event_id_and_seq_is_the_cursor() -> None:
    store, _ = _store()
    ev = TraceEvent( event_id="s.1", object_id="x:1", at=1.0, kind="progress", message="hi" )
    assert store.put_event( ev ) is True
    assert store.put_event( ev ) is False
    store.put_event( ev.model_copy( update={ "event_id": "s.2", "at": 2.0 }))
    got = store.events( after_seq=0 )
    assert [ e.event_id for e in got ] == [ "s.1", "s.2" ]
    assert [ e.event_id for e in store.events( after_seq=got[ 0 ].seq )] == [ "s.2" ]
    assert store.object( "x:1" ).kind == "unknown"          # an event about an unrecorded object still shows up


def test_migrate_down_drops_only_its_own_tables() -> None:
    store, path = _store()
    store.close()
    conn = sqlite3.connect( str( path ))
    conn.execute( "CREATE TABLE someone_elses ( x )" )
    assert version_of( conn ) == 1
    assert migrate( conn, 0 ) == 0
    names = { r[ 0 ] for r in conn.execute( "SELECT name FROM sqlite_master WHERE type = 'table'" )}
    assert "someone_elses" in names and not any( n.startswith( "trace_" ) for n in names )
    assert migrate( conn ) == 1
    conn.close()


def test_read_only_store_cannot_write() -> None:
    store, path = _store()
    store.close()
    ro = SqliteTraceStore( path, read_only=True )
    try:
        ro.put_event( TraceEvent( event_id="s.1", object_id="x:1", at=1.0, kind="progress" ))
    except sqlite3.OperationalError:
        return
    raise AssertionError( "a read-only store wrote" )


def test_prune_drops_old_events_and_finished_objects_only() -> None:
    store, _ = _store()
    clock = Clock( 1000.0 )
    t = _tracer( store, clock )
    old_done = t.begin( "w.x", "old:done" )
    old_done.succeed()
    old_open = t.begin( "w.x", "old:open" )
    clock.t = 1000.0 + 30 * 86400
    fresh = t.begin( "w.x", "fresh" )
    fresh.progress( "still here" )
    events, objects = store.prune( 14 * 86400, now=clock.t )
    assert events >= 3 and objects == 1
    assert store.object( "old:done" ) is None
    assert store.object( "old:open" ) is not None              # unfinished: kept, and it will read as stale
    assert store.object( "fresh" ) is not None


def test_object_without_run_inherits_it_through_task_id() -> None:
    store, _ = _store()
    t = _tracer( store, Clock())
    with trace_scope( run_id="toyota-req-1" ):
        t.begin( "toyota.bridge_submit", "submit:1", task_id="t_abc" ).succeed()
    t.begin( "bridge.task_run", "bridge.task_run:t_abc:1", task_id="t_abc" )      # the worker never saw the request id
    assert store.object( "bridge.task_run:t_abc:1" ).run_id == "toyota-req-1"
    assert { o.object_id for o in store.objects( run_id="toyota-req-1" )} == { "submit:1", "bridge.task_run:t_abc:1" }


# -- tracer --------------------------------------------------------------------------------------

def test_scope_and_parent_are_inherited() -> None:
    store, _ = _store()
    t = _tracer( store, Clock())
    with trace_scope( run_id="r1", task_id="t1" ):
        with t.begin( "a.run", "a:1" ):
            child = t.begin( "a.step", "a:1:s" )
    o = store.object( "a:1:s" )
    assert o.parent_id == "a:1" and o.run_id == "r1" and o.task_id == "t1"
    assert store.object( "a:1" ).state == "succeeded"
    assert "a:1:s" in store.dependents( "a:1" )
    assert child.done is False


def test_construct_failure_is_recorded_and_reraised() -> None:
    store, _ = _store()
    t = _tracer( store, Clock())

    def broken() -> object:
        raise RuntimeError( "no config password=hunter2 here" )

    try:
        t.construct( "agent.thing", broken, interface="IAgentAdapter", object_id="agent.thing:1" )
    except RuntimeError:
        pass
    else:
        raise AssertionError( "construct swallowed the error" )
    o = store.object( "agent.thing:1" )
    assert o.state == "construct_failed" and o.error[ "type" ] == "RuntimeError" and o.interface == "IAgentAdapter"
    made = t.construct( "agent.ok", lambda: Clock(), object_id="agent.ok:1" )
    assert isinstance( made, Clock ) and store.object( "agent.ok:1" ).implementation == "Clock"


def test_safe_sink_surfaces_telemetry_failure_without_raising() -> None:
    store, _ = _store()

    class Flaky:
        broken = True

        def put_object( self, obj ):
            if self.broken:
                raise sqlite3.OperationalError( "disk I/O error" )
            store.put_object( obj )

        def put_event( self, ev ):
            if self.broken:
                raise sqlite3.OperationalError( "disk I/O error" )
            return store.put_event( ev )

    flaky, err = Flaky(), io.StringIO()
    sink = SafeSink( flaky, Clock(), stream=err )
    t = ObjectTracer( sink, clock=Clock(), host="h", pid=1, process_session="sessX" )
    tr = t.begin( "w.x", "w:1" )
    tr.progress( "lost" )
    assert sink.failures >= 2 and "disk I/O error" in err.getvalue()
    assert err.getvalue().count( "object_trace" ) == 1          # said once, counted after
    flaky.broken = False
    tr.progress( "back" )
    tel = [ o for o in store.objects( kind="telemetry*" )]
    assert tel and tel[ 0 ].state == "failed"
    assert any( "lost" in e.message or "dropped" in e.message for e in store.latest_events( tel[ 0 ].object_id ))


def test_bad_ids_never_raise_into_the_workflow() -> None:
    store, _ = _store()
    t = _tracer( store, Clock())
    tr = t.begin( "w.x", "bad id with spaces & 'quotes'", task_id="t 1" )
    tr.succeed()
    assert store.object( "bad_id_with_spaces____quotes_" ) is not None
    tr2 = t.begin( "NOT A KIND", "w:2" )                        # invalid kind: dropped, workflow unaffected
    tr2.progress( "nothing" )
    tr2.succeed()
    assert store.object( "w:2" ) is None


# -- liveness ------------------------------------------------------------------------------------

def test_a_record_alone_is_never_proof_of_life() -> None:
    o = TraceObject( object_id="w:1", kind="w.x", created_at=0.0, state="running", heartbeat_seconds=10,
                     last_event_at=100.0, last_event_kind="heartbeat", host="h", pid=1 )
    assert view( o, 105.0, pid_alive=NO_PID ).display == "heartbeat"
    stale = view( o, 200.0, pid_alive=NO_PID )
    assert stale.display == "stale" and stale.alive is False
    gone = view( o, 105.0, pid_alive=lambda h, p: False )
    assert gone.display == "stale" and "gone" in gone.reason
    assert view( o.model_copy( update={ "last_event_kind": "waiting" }), 105.0, pid_alive=NO_PID ).display == "waiting"
    done = o.model_copy( update={ "state": "failed", "error": { "message": "boom" }})
    assert view( done, 10_000.0, pid_alive=NO_PID ).display == "failed"


# -- ADW hooks -------------------------------------------------------------------------------

def test_adw_hooks_chain_phases_and_record_where_it_stopped() -> None:
    store, _ = _store()
    t = _tracer( store, Clock())
    task = { "id": None }

    def claim() -> None:
        task[ "id" ] = "t_9"

    def explode() -> None:
        raise ValueError( "agent refused" )

    try:
        with AdwRunTrace( t, "adw_demo", "abc123", task_id_of=lambda: task[ "id" ]) as hooks:
            hooks.phase( "claim_task", claim )()
            hooks.phase( "execute_agent", explode )()
    except ValueError:
        pass
    run = store.object( "adw.run:abc123" )
    claim_o, exec_o = store.object( "adw.phase:abc123:claim_task" ), store.object( "adw.phase:abc123:execute_agent" )
    assert run.state == "failed" and claim_o.state == "succeeded" and exec_o.state == "failed"
    assert exec_o.deps == [ "adw.phase:abc123:claim_task" ] and exec_o.task_id == "t_9" and exec_o.parent_id == run.object_id
    assert claim_o.task_id is None                             # nothing claimed yet when it began


# -- inspector: the four questions ------------------------------------------------------------

def _notes_world( store, clock: Clock, rendered_revision: str | None ) -> None:
    t = _tracer( store, clock )
    with trace_scope( run_id="req-notes-1" ):
        t.begin( "toyota.bridge_submit", "toyota.bridge_submit:t_n1", task_id="t_n1" ).succeed({ "task_id": "t_n1" })
    with AdwRunTrace( t, "adw_bridge_worker", "w1", task_id_of=lambda: "t_n1" ) as hooks:
        for name in ( "claim_task", "execute_agent", "publish_result" ):
            hooks.phase( name, lambda: None )()
    task = t.begin( "bridge.task_run", "bridge.task_run:t_n1:1", task_id="t_n1", attempt=1, adw_id="w1" )
    agent = t.begin( "agent.notes", "agent.notes:t_n1:1", task_id="t_n1",
                     inputs={ "request": { "notes": "marker_write", "marker": "NOTESDIAG-aaaaaaaaaaaa" }})
    clock.t += 2
    agent.succeed({ "kind": "succeeded", "details": { "filename": "diag.md", "revision_before": "r0", "revision_after": "rev123456789xyz",
                                                      "read_back_ok": True, "store_marker": True, "served_marker": True,
                                                      "revisions_agree": True }})
    task.succeed({ "summary": "wrote marker", "gateway_state": "succeeded" })
    if rendered_revision is not None:
        clock.t += 3
        t.begin( "browser.render", "browser.render:1", implementation="ChromeDevTools",
                 inputs={ "filename": "diag.md" }).succeed({ "revision": rendered_revision, "marker_seen": True })


def test_inspector_answers_the_four_questions_with_evidence() -> None:
    store, _ = _store()
    clock = Clock()
    _notes_world( store, clock, rendered_revision="rev123456789xyz" )
    ins = Inspector( store, clock, None, pid_alive=NO_PID )
    latest = ins.ask( "latest_notes_update" )
    assert latest.found and "t_n1" in latest.answer and "diag.md" in latest.answer and latest.evidence
    where = ins.ask( "where_stopped", "t_n1" )
    assert where.found and "publish_result" in where.answer and "req-notes-1" in where.answer, where.answer
    state = ins.ask( "object_state", "agent.notes:t_n1:1" )
    assert state.found and "succeeded" in state.answer
    sr = ins.ask( "saved_and_rendered" )
    assert sr.found and "Saved: yes" in sr.answer and "Rendered: yes" in sr.answer, sr.answer


def test_inspector_says_what_is_missing_rather_than_guessing() -> None:
    store, _ = _store()
    clock = Clock()
    ins = Inspector( store, clock, None, pid_alive=NO_PID )
    for q in ( "latest_notes_update", "where_stopped", "saved_and_rendered" ):
        a = ins.ask( q )
        assert not a.found and a.missing, q
    assert not ins.ask( "object_state", "nope" ).found
    _notes_world( store, clock, rendered_revision=None )
    sr = ins.ask( "saved_and_rendered" )
    assert "Rendered: unknown" in sr.answer and sr.missing
    _notes_world( store, clock, rendered_revision="old-revision" )
    assert "Rendered: NO" in Inspector( store, clock, None, pid_alive=NO_PID ).ask( "saved_and_rendered" ).answer


def test_snapshot_groups_by_run_and_changes_are_incremental() -> None:
    store, _ = _store()
    clock = Clock()
    _notes_world( store, clock, rendered_revision="rev123456789xyz" )
    ins = Inspector( store, clock, None, pid_alive=NO_PID )
    snap = ins.snapshot()
    runs = { g[ "run_id" ]: g for g in snap[ "runs" ]}
    assert "req-notes-1" in runs
    assert { o[ "object_id" ] for o in runs[ "req-notes-1" ][ "objects" ]} >= { "toyota.bridge_submit:t_n1", "agent.notes:t_n1:1" }
    assert all( "inputs" not in o for g in snap[ "runs" ] for o in g[ "objects" ])
    cursor = snap[ "max_seq" ]
    assert ins.changes( cursor )[ "events" ] == []
    t = _tracer( store, clock )
    t.begin( "w.x", "late:1" ).heartbeat()
    ch = ins.changes( cursor )
    assert { o[ "object_id" ] for o in ch[ "objects" ]} == { "late:1" } and ch[ "max_seq" ] > cursor


# -- sssf reader and service --------------------------------------------------------------------

def _sssf( path: Path ) -> None:
    conn = sqlite3.connect( str( path ))
    conn.executescript( """
        CREATE TABLE sessions ( adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT, status TEXT, engineer TEXT,
                                started_at TEXT, ended_at TEXT );
        CREATE TABLE phases ( phase_id TEXT, adw_id TEXT, seq INT, name TEXT, kind TEXT, owner TEXT, description TEXT,
                              status TEXT, attempt INT, retries INT, error TEXT, started_at TEXT, ended_at TEXT );
        CREATE TABLE processes ( id INTEGER PRIMARY KEY, adw_id TEXT, kind TEXT, name TEXT, pid INT, command TEXT,
                                 started_at TEXT, ended_at TEXT );
        INSERT INTO sessions VALUES ( 'a1', 'adw_demo', 'r', 'fail', 'e', '2026-11-05T10:00:00+00:00', '2026-11-05T10:01:00+00:00' );
        INSERT INTO phases VALUES ( 'p1', 'a1', 1, 'plan', 'k', 'o', 'Plan', 'success', 1, 0, NULL,
                                    '2026-11-05T10:00:00+00:00', '2026-11-05T10:00:30+00:00' );
        INSERT INTO phases VALUES ( 'p2', 'a1', 2, 'build', 'k', 'o', 'Build', 'fail', 1, 0, 'boom',
                                    '2026-11-05T10:00:30+00:00', '2026-11-05T10:01:00+00:00' );
    """ )
    conn.commit()
    conn.close()


def test_sssf_reader_maps_sessions_and_phases_read_only() -> None:
    d = Path( tempfile.mkdtemp())
    _sssf( d / "sssf.db" )
    r = SssfReader( d / "sssf.db" )
    assert r.status() == "readable"
    s = r.session( "a1" )
    assert s.state == "failed" and s.implementation == "adw_demo"
    ph = r.phases( "a1" )
    assert [ p.state for p in ph ] == [ "succeeded", "failed" ] and ph[ 1 ].deps == [ ph[ 0 ].object_id ]
    assert r.adw_names()[ 0 ][ :2 ] == ( "adw_demo", 1 )
    assert SssfReader( d / "absent.db" ).status() == "absent"


def test_service_routes() -> None:
    store, path = _store()
    clock = Clock()
    _notes_world( store, clock, rendered_revision="rev123456789xyz" )
    store.close()
    d = path.parent
    _sssf( d / "sssf.db" )
    ui = d / "public"
    ui.mkdir()
    ( ui / "index.html" ).write_text( "<p>ui</p>" )
    ( d / "secret.txt" ).write_text( "outside" )
    adws = d / "adws"
    ( adws / "harness_pkg" ).mkdir( parents=True )
    ( adws / "sssf_roles.py" ).write_text( "X = 1\n" )
    ( adws / "harness_pkg" / "__init__.py" ).write_text( "from sssf_roles import X\n" )
    ( adws / "adw_demo.py" ).write_text( "from harness_pkg import X\n" )
    ( adws / "adw_self.py" ).write_text( "with AdwRunTrace( t, 'adw_self' ):\n    pass\n" )
    ( adws / "adw_bare.py" ).write_text( "import json\n" )
    app = InspectionApp( path, d / "sssf.db", ui, build="test", adws=adws )
    status, _, body = app.handle( "/api/health", {})
    h = json.loads( body )
    assert status == 200 and h[ "build" ] == "test" and h[ "schema_version" ] == 1 and h[ "events" ] > 0 and h[ "sssf" ] == "readable"
    assert json.loads( app.handle( "/api/snapshot", {})[ 2 ])[ "sssf_sessions" ]
    assert json.loads( app.handle( "/api/ask/saved_and_rendered", {})[ 2 ])[ "found" ] is True
    assert app.handle( "/api/ask/rm_rf", {})[ 0 ] == 404
    assert app.handle( "/api/events", { "after_seq": [ "x" ]})[ 0 ] == 400
    one = json.loads( app.handle( "/api/objects/agent.notes:t_n1:1", {})[ 2 ])
    assert one[ "display" ] == "succeeded" and one[ "events" ]
    assert json.loads( app.handle( "/api/objects/sssf.run:a1", {})[ 2 ])[ "display" ] == "failed"
    fx = json.loads( app.handle( "/fx/object/select", { "object_view_id": [ "agent.notes:t_n1:1" ]})[ 2 ])
    data = json.loads( fx[ "object_data" ])
    assert fx[ "object_view_id" ] == "agent.notes:t_n1:1" and data[ "logObjects" ] and data[ "monitorLed" ][ "ledText" ]
    assert isinstance( json.loads( app.handle( "/fx/object/selectAll", {})[ 2 ]), list )
    inv = json.loads( app.handle( "/api/inventory", {})[ 2 ])
    hooks = { r[ "adw" ]: ( r[ "hook" ], r[ "via" ]) for r in inv[ "adws" ]}
    assert hooks == { "adw_bare": ( "not hooked", None ), "adw_demo": ( "shared executor (sssf_roles)", "harness_pkg" ),
                      "adw_self": ( "self-traced (AdwRunTrace)", None )}, hooks
    assert inv[ "totals" ][ "not hooked" ] == 1 and "adw_bridge_worker" in inv[ "seen_but_not_on_disk" ]
    assert app.handle( "/", {})[ 2 ] == b"<p>ui</p>"
    assert app.handle( "/../secret.txt", {})[ 0 ] == 404
    assert app.handle( "/%2e%2e/secret.txt", {})[ 0 ] == 404


def test_shared_executor_traces_runs_and_phases_and_never_changes_them() -> None:
    from object_trace import factory
    from object_trace.sssf_hooks import traced_executor
    store, path = _store()
    saved, factory._shared = factory._shared, ObjectTracer( store, clock=Clock())

    class Base:
        def __init__( self, logger: object ) -> None:
            self.logger = logger

        def execute( self, run: object, phase: object ) -> object:
            return phase.act( None )

    class Params:
        def __init__( self, name: str ) -> None:
            self.name, self.description, self.owner, self.kind = name, name + " step", "me", "code"

    class Phase:
        def __init__( self, name: str, fail: bool = False ) -> None:
            self.params, self._fail = Params( name ), fail

        def act( self, handle: object ) -> object:
            if self._fail:
                raise ValueError( "boom" )
            return self.params.name + "-out"

    class Run:
        def __init__( self, adw_id: str ) -> None:
            self.adw_id, self.finished = adw_id, None

        def finish( self, accepted: bool = True, reason: str = "" ) -> int:
            self.finished = ( accepted, reason )
            return 0 if accepted else 1

    try:
        Traced = traced_executor( Base )
        ex, run = Traced( "log" ), Run( "abc12345" )
        assert Traced.__name__ == "Base" and ex.logger == "log"
        assert ex.execute( run, Phase( "claim" )) == "claim-out"
        assert ex.execute( run, Phase( "claim" )) == "claim-out"
        try:
            ex.execute( run, Phase( "act", fail=True ))
            raise AssertionError( "the phase error must re-raise" )
        except ValueError:
            pass
        assert run.finish( False, "act failed" ) == 1 and run.finished == ( False, "act failed" )
        objs = { o.object_id: o for o in store.objects( limit=50 )}
        assert objs[ "adw.run:abc12345" ].state == "failed"
        assert objs[ "adw.phase:abc12345:claim" ].state == "succeeded"
        assert objs[ "adw.phase:abc12345:claim:2" ].deps == [ "adw.phase:abc12345:claim" ]
        assert objs[ "adw.phase:abc12345:act" ].state == "failed" and objs[ "adw.phase:abc12345:act" ].parent_id == "adw.run:abc12345"
        with trace_scope( adw_id="outer" ):              # an AdwRunTrace already covers it: nothing new is traced
            ex.execute( Run( "zzz99999" ), Phase( "x" ))
        assert "adw.run:zzz99999" not in { o.object_id for o in store.objects( limit=50 )}
    finally:
        factory._shared = saved


def test_null_sink_tracer_is_harmless() -> None:
    t = ObjectTracer( NullSink())
    with t.begin( "w.x" ) as tr:
        tr.heartbeat()
        tr.progress( "p" )


def test_constructed_worker_can_close_and_run_ids_are_populated() -> None:
    store, _ = _store()
    tracer = _tracer( store, Clock())
    with AdwRunTrace( tracer, "adw_demo", "lifecycle" ) as hooks:
        made = tracer.construct( "bridge.worker_app", lambda: Clock(), object_id="app:lifecycle",
                                 on_created=lambda trace: hooks.closing(
                                     lambda reason: trace.succeed( message="closed" )))
        hooks.phase( "finalize_task", lambda: None )()
    app = store.object( "app:lifecycle" )
    assert app.state == "succeeded" and app.run_id == "lifecycle"
    run = store.object( "adw.run:lifecycle" )
    assert run.outputs[ "scope" ] == "orchestration"
    assert store.object( "adw.phase:lifecycle:finalize_task" ).run_id == app.run_id


if __name__ == "__main__":
    failed = 0
    tests = [( n, f ) for n, f in sorted( globals().items()) if n.startswith( "test_" ) and callable( f )]
    for name, fn in tests:
        try:
            fn()
            print( f"ok    { name }" )
        except Exception:                     # noqa: BLE001
            failed += 1
            print( f"FAIL  { name }" )
            traceback.print_exc()
    print( f"\n{ len( tests ) - failed }/{ len( tests )} passed" )
    raise SystemExit( 1 if failed else 0 )

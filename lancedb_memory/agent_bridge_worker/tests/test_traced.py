"""TracingGateway and TracingAgent: they record, and they change nothing. Offline; temp store only.

    cd lancedb_memory && .venv/bin/python agent_bridge_worker/tests/test_traced.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.traced import TracingAgent, TracingGateway       # noqa: E402
from agent_bridge_worker.models import AgentOutcome, Beat, ClaimedTask, OutcomeKind # noqa: E402
from object_trace.adapters.sqlite_store import SqliteTraceStore                     # noqa: E402
from object_trace.adw_hooks import AdwRunTrace                                       # noqa: E402
from object_trace.tracer import ObjectTracer                                         # noqa: E402

TASK = dict( task_id="t_" + "a" * 24, lease_token="L" * 40, lease_expires_at=0, lease_seconds=90, attempt=1,
             agent="notes", workspace="notes", operation="implement", approval_mode="workspace_write",
             prompt=json.dumps({ "notes": "marker_write", "marker": "NOTESDIAG-0123456789ab" }))


class FakeGateway:
    def __init__( self, fail_complete: bool = False ) -> None:
        self.calls: list[ tuple ] = []
        self._fail = fail_complete

    def next( self ):
        self.calls.append(( "next", ))
        return TASK[ "task_id" ]

    def claim( self, task_id, worker_id ):
        self.calls.append(( "claim", task_id, worker_id ))
        return ClaimedTask( **TASK )

    def heartbeat( self, task ):
        self.calls.append(( "heartbeat", ))
        return Beat( cancel_requested=False, lease_expires_at=5 )

    def event( self, task, kind, message ):
        self.calls.append(( "event", kind, message ))

    def complete( self, task, ok, summary, body, performer, details=None ):
        self.calls.append(( "complete", ok, summary, body, performer, details ))
        if self._fail:
            raise RuntimeError( "409 lease gone" )
        return "succeeded"

    def fail( self, task, reason, retryable, cancelled ):
        self.calls.append(( "fail", reason, retryable, cancelled ))
        return "cancelled" if cancelled else "failed"


class FakeAgent:
    def __init__( self, outcome: AgentOutcome | Exception ) -> None:
        self._outcome = outcome

    def run( self, task, workdir, keep_alive ):
        keep_alive()
        if isinstance( self._outcome, Exception ):
            raise self._outcome
        return self._outcome


def _world():
    store = SqliteTraceStore( Path( tempfile.mkdtemp()) / "t.db" )
    return store, ObjectTracer( store, host="h", pid=1, process_session="wk" )


def test_gateway_forwards_unchanged_and_records_the_task() -> None:
    store, tracer = _world()
    inner = FakeGateway()
    with AdwRunTrace( tracer, "adw_bridge_worker", "run1" ) as hooks:
        gw = TracingGateway( inner, tracer, lambda: hooks.current )
        hooks.closing( gw.close )

        def claim() -> None:
            task = gw.claim( gw.next(), "wsl-x" )
            assert task.lease_token == "L" * 40                # the real object comes back untouched
            gw.heartbeat( task )
            gw.event( task, "phase", "claimed" )

        hooks.phase( "claim_task", claim )()
        task = ClaimedTask( **TASK )
        hooks.phase( "execute_agent", lambda: gw.heartbeat( task ))()
        assert gw.complete( task, True, "wrote marker", "the body", "NotesAgent", { "filename": "d.md" }) == "succeeded"
    assert [ c[ 0 ] for c in inner.calls ] == [ "next", "claim", "heartbeat", "event", "heartbeat", "complete" ]
    assert inner.calls[ -1 ][ 5 ] == { "filename": "d.md" }
    o = store.object( f"bridge.task_run:{ TASK[ 'task_id' ]}:1" )
    assert o.state == "succeeded" and o.adw_id == "run1" and o.outputs[ "gateway_state" ] == "succeeded"
    assert o.inputs[ "request" ] == { "notes": "marker_write", "marker": "NOTESDIAG-0123456789ab" }
    dumped = json.dumps( o.model_dump())
    assert "L" * 40 not in dumped and "the body" not in dumped and set( o.inputs[ "prompt" ]) == { "sha256", "bytes" }
    kinds = [ e.kind for e in store.latest_events( o.object_id )]
    assert "heartbeat" in kinds and "progress" in kinds
    phase = store.latest_events( "adw.phase:run1:execute_agent" )
    assert any( e.kind == "heartbeat" for e in phase )       # the lease heartbeat kept the phase alive too


def test_unpublished_task_is_closed_not_left_running() -> None:
    store, tracer = _world()
    try:
        with AdwRunTrace( tracer, "adw_bridge_worker", "run2" ) as hooks:
            gw = TracingGateway( FakeGateway( fail_complete=True ), tracer )
            hooks.closing( gw.close )
            task = gw.claim( TASK[ "task_id" ], "w" )
            gw.complete( task, True, "s", "b", "p" )
    except RuntimeError:
        pass
    else:
        raise AssertionError( "the gateway's error was swallowed" )
    o = store.object( f"bridge.task_run:{ TASK[ 'task_id' ]}:1" )
    assert o.state == "cancelled" and any( e.kind == "error" for e in store.latest_events( o.object_id ))


def test_gateway_fail_and_cancel_are_recorded() -> None:
    store, tracer = _world()
    gw = TracingGateway( FakeGateway(), tracer )
    task = gw.claim( TASK[ "task_id" ], "w" )
    assert gw.fail( task, "client cancelled", False, True ) == "cancelled"
    assert store.object( f"bridge.task_run:{ TASK[ 'task_id' ]}:1" ).state == "cancelled"


def test_scan_uploads_pass_through() -> None:
    store, tracer = _world()
    inner = FakeGateway()
    inner.upload_blob = lambda scan_id, sha, data: inner.calls.append(( "upload", scan_id, data ))
    inner.post_scan   = lambda scan_id, manifest: ( inner.calls.append(( "post", manifest )), { "verdict": "ok" })[ 1 ]
    gw = TracingGateway( inner, tracer )
    gw.claim( TASK[ "task_id" ], "w" )
    gw.upload_blob( "s1", "f" * 64, b"PAGEBYTES" )
    assert gw.post_scan( "s1", { "pages": 1 }) == { "verdict": "ok" }
    assert ( "upload", "s1", b"PAGEBYTES" ) in inner.calls and ( "post", { "pages": 1 }) in inner.calls
    assert "PAGEBYTES" not in json.dumps([ e.model_dump() for e in store.latest_events( f"bridge.task_run:{ TASK[ 'task_id' ]}:1" )])


def test_agent_outcomes_and_exceptions() -> None:
    store, tracer = _world()
    task = ClaimedTask( **TASK )
    ok = AgentOutcome( kind=OutcomeKind.SUCCEEDED, summary="wrote", body="private words", performer="NotesAgent",
                       details={ "filename": "d.md", "revision_after": "r1", "read_back_ok": True })
    assert TracingAgent( FakeAgent( ok ), tracer, "notes" ).run( task, Path( "/tmp" ), lambda: "continue" ) is ok
    o = store.object( f"agent.notes:{ TASK[ 'task_id' ]}:1" )
    assert o.state == "succeeded" and o.outputs[ "details" ][ "revision_after" ] == "r1" and o.interface == "IAgentAdapter"
    assert "private words" not in json.dumps( o.model_dump())
    bad = AgentOutcome( kind=OutcomeKind.REFUSED, summary="policy said no" )
    TracingAgent( FakeAgent( bad ), tracer, "codex-writer" ).run( task, Path( "/tmp" ), lambda: "continue" )
    assert store.object( f"agent.codex_writer:{ TASK[ 'task_id' ]}:1" ).state == "failed"
    try:
        TracingAgent( FakeAgent( OSError( "post office down" )), tracer, "printer" ).run( task, Path( "/tmp" ), lambda: "continue" )
    except OSError:
        pass
    else:
        raise AssertionError( "the adapter's exception was swallowed" )
    err = store.object( f"agent.printer:{ TASK[ 'task_id' ]}:1" )
    assert err.state == "failed" and err.error[ "type" ] == "OSError"


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

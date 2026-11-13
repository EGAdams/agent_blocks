"""Object Debugger safety and real SQLite/post-office integration, offline.

    .venv/bin/python -m agent_comms.tests.test_object_debugger
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent_bridge_worker.policy import scoped_operations
from agent_bridge_worker.workspaces import ConfiguredWorkspaces
from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.executors.object_debugger import MAX_OUTPUT_BYTES, ObjectDebuggerExecutor
from agent_comms.factory import WORKSPACE, build_post_office
from agent_comms.models import Envelope, MessageKind, TaskRequest
from object_trace.adapters.sqlite_store import SqliteTraceStore
from object_trace.models import TraceEvent, TraceObject

TASK = "t_19133b18bee29034b4be59c7"
SECRET = "sk-" + "exampleTestCredential" * 3


class Clock:
    def now( self ) -> float:
        return 1000.0


def envelope( text: str, **kwargs ) -> Envelope:
    return Envelope( id="test", correlation_id="test", kind=MessageKind.REQUEST,
                     sender="chatgpt-bridge", recipient="object-debugger", task_type="inspect", text=text,
                     **kwargs )


def execute( db: Path, text: str, **kwargs ):
    return asyncio.run( ObjectDebuggerExecutor( db, clock=Clock(), **kwargs ).execute( envelope( text )))


def seed( path: Path, count: int = 5 ) -> None:
    store = SqliteTraceStore( path )
    try:
        store.put_object( TraceObject( object_id="bridge:example", kind="bridge.task_run", task_id=TASK,
                                      created_at=100.0, state="succeeded", last_event_at=999.0,
                                      outputs={ "gateway_state": "succeeded", "summary": "Hello World" }))
        for i in range( count ):
            store.put_event( TraceEvent( event_id=f"event:{ i }", object_id="bridge:example", task_id=TASK,
                                        at=999.0, kind="progress", message=f"step { i }" ))
    finally:
        store.close()


def test_missing_source_is_unavailable_and_never_created() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "missing" / "trace.db"
        for text in ( "status", "recent", f"task { TASK }" ):
            out = execute( db, text )
            assert not out.ok and "unavailable" in out.text
        assert not db.parent.exists()


def test_status_reports_fresh_stale_and_empty_without_claiming_a_heartbeat() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "trace.db"
        SqliteTraceStore( db ).close()
        assert execute( db, "status" ).details[ "freshness" ] == "empty"
        seed( db )
        fresh = execute( db, "source connectivity and freshness" )
        assert fresh.ok and fresh.details[ "freshness" ] == "fresh"
        assert fresh.details[ "schema_version" ] == 1 and fresh.details[ "events" ] == 5
        stale = execute( db, "status", stale_seconds=0.5 )
        assert stale.ok and stale.details[ "freshness" ] == "stale"
        assert stale.details[ "age_seconds" ] == 1.0


def test_recent_returns_the_latest_events_and_caps_the_limit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "trace.db"
        seed( db, 250 )
        for text, limit in (( "recent 3", 3 ), ( "events", 50 ), ( "lifecycle 999999", 200 ),
                            ( "changes 0", 1 ), ( "recent -2", 1 ), ( "recent " + "9" * 7000, 200 )):
            out = execute( db, text )
            events = out.details[ "events" ]
            assert out.ok and out.details[ "limit" ] == limit, out
            assert len( events ) <= limit and events[ -1 ][ "seq" ] == 250
            assert len( out.text.encode( "utf-8" )) <= MAX_OUTPUT_BYTES
            assert "objects" not in out.details


def test_task_lookup_returns_evidence_and_missing_records_honestly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "trace.db"
        seed( db )
        out = execute( db, f"Show lifecycle events for task { TASK }." )
        assert out.ok and "gateway state succeeded" in out.text
        assert out.details[ "evidence" ] and out.details[ "missing" ]
        missing = execute( db, "task t_" + "0" * 24 )
        assert not missing.ok and "no traced Bridge task" in missing.text


def test_invalid_requests_never_open_the_source() -> None:
    with patch( "agent_comms.executors.object_debugger.SqliteTraceStore", side_effect=AssertionError( "opened" )) as store:
        for text in ( "task", "task t_bad", "task t_" + "f" * 25, "t_" + "A" * 24,
                      "task /etc/passwd", f"task { TASK } t_" + "0" * 24, "", "x" * 8001 ):
            out = execute( Path( "/never" ), text )
            assert not out.ok, text
        store.assert_not_called()


def test_executor_rejects_other_senders_recipients_and_operations() -> None:
    executor = ObjectDebuggerExecutor( Path( "/never" ))
    with patch( "agent_comms.executors.object_debugger.SqliteTraceStore" ) as store:
        for change in ({ "sender": "sydney" }, { "recipient": "codex" }, { "task_type": "implement" }):
            out = asyncio.run( executor.execute( envelope( "status" ).model_copy( update=change )))
            assert not out.ok
        store.assert_not_called()


def test_read_requests_open_read_only_and_leave_the_database_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "trace.db"
        seed( db )
        before = db.read_bytes(), db.stat().st_mtime_ns
        with patch( "agent_comms.executors.object_debugger.SqliteTraceStore", wraps=SqliteTraceStore ) as store:
            for text in ( "status", "recent 2", f"task { TASK }" ):
                assert execute( db, text ).ok
            assert store.call_count == 3
            assert all( call.kwargs == { "read_only": True } for call in store.call_args_list )
        assert before == ( db.read_bytes(), db.stat().st_mtime_ns )


def test_secrets_in_health_answers_and_event_details_withhold_everything() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "trace.db"
        seed( db )
        with patch( "agent_comms.executors.object_debugger.source_build", return_value=SECRET ):
            out = execute( db, "status" )
            assert not out.ok and "withheld" in out.text and not out.details
            assert SECRET not in out.model_dump_json()
        store = SqliteTraceStore( db )
        obj = store.object( "bridge:example" )
        store.put_object( obj.model_copy( update={ "outputs": { "summary": SECRET }}))
        store.put_event( TraceEvent( event_id="secret", object_id="bridge:example", at=999.0,
                                    kind="output", data={ "nested": [ SECRET ]}))
        store.close()
        for text in ( f"task { TASK }", "recent" ):
            out = execute( db, text )
            assert not out.ok and "withheld" in out.text and not out.details
            assert SECRET not in out.model_dump_json()


def test_oversized_events_keep_valid_json_and_scan_before_trimming() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path( tmp ) / "trace.db"
        seed( db )
        store = SqliteTraceStore( db )
        store.put_event( TraceEvent( event_id="huge", object_id="bridge:example", at=999.0,
                                    kind="progress", data={ "large": "é" * 30000 }))
        store.put_event( TraceEvent( event_id="newest", object_id="bridge:example", at=999.0, kind="progress" ))
        store.close()
        out = execute( db, "recent" )
        assert out.ok and len( out.text.encode( "utf-8" )) <= MAX_OUTPUT_BYTES
        assert json.loads( out.text.split( "\n", 1 )[ 1 ])[ "truncated" ]
        assert out.details[ "events" ][ -1 ][ "event_id" ] == "newest"
        store = SqliteTraceStore( db )
        store.put_event( TraceEvent( event_id="huge-secret", object_id="bridge:example", at=999.0,
                                    kind="progress", data={ "large": "x" * 30000, "secret": SECRET }))
        store.close()
        out = execute( db, "recent" )
        assert not out.ok and "possible secret" in out.text and SECRET not in out.model_dump_json()


def test_source_errors_never_echo_exception_text() -> None:
    with patch( "agent_comms.executors.object_debugger.SqliteTraceStore", side_effect=RuntimeError( SECRET )):
        out = execute( Path( "/never" ), "status" )
        assert not out.ok and "unavailable" in out.text and SECRET not in out.model_dump_json()


def test_json_credential_fields_are_withheld() -> None:
    executor = ObjectDebuggerExecutor( Path( "/never" ))
    for key in ( "password", "api_key", "access_token" ):
        out = executor._result( True, "events", { "nested": [{ key: "exampleCredential123456" }]})
        assert not out.ok and "possible secret" in out.text and not out.details
        assert "exampleCredential" not in out.model_dump_json()


def test_registry_policy_workspace_and_real_post_office_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path( tmp )
        db = root / "trace.db"
        seed( db )
        with patch.dict( os.environ, { "OBJECT_TRACE_DB": str( db )}):
            kit = build_post_office( root=root / "mail", launcher=RecordingLauncher(), forward_failures_to_mechanic=False )
            card = kit.registry.card( "object-debugger" )
            assert card.runtime == "object_trace" and card.accepts_from == [ "chatgpt-bridge", "toyota-bridge", "sydney-bridge" ]
            assert scoped_operations( kit.registry, "chatgpt-bridge" )[ "object-debugger" ] == { "inspect": False }
            config = ConfiguredWorkspaces.load( WORKSPACE / "lancedb_memory/agent_bridge_worker/workspaces.json", protected=[] )
            assert config.resolve( "object_trace" ).is_dir() and "object_trace" not in config.write_names()
            for sender in ( "sydney", "toyota", "unknown" ):
                refused = kit.dispatcher.send( TaskRequest( sender=sender, recipient=card.name, task_type="inspect", text="status", wait_seconds=0 ))
                assert not refused.accepted
            receipt = kit.dispatcher.send( TaskRequest( sender="chatgpt-bridge", recipient=card.name, task_type="inspect", text="recent 2", wait_seconds=0 ))
            assert receipt.accepted
            asyncio.run( kit.worker_for( card.name ).work_one())
            reply = kit.replies.take( "chatgpt-bridge", receipt.task_id )
            assert reply.outcome.value == "succeeded" and "step 4" in reply.result
            assert reply.performer == "object_trace:inspector"
            receipt = kit.dispatcher.send( TaskRequest( sender="toyota-bridge", recipient=card.name, task_type="inspect", text="status", wait_seconds=0 ))
            assert receipt.accepted
            asyncio.run( kit.worker_for( card.name ).work_one())
            assert kit.replies.take( "toyota-bridge", receipt.task_id ).outcome.value == "succeeded"


if __name__ == "__main__":
    tests = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
    for test in tests:
        test()
        print( f"ok  { test.__name__ }" )
    print( f"{ len( tests ) } passed" )

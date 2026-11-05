"""The Worker Activity snapshot, offline: envelope files in a tmpdir, no gateway.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_activity_snapshot
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.activity_snapshot import build_rows, duration, safe_error, write_snapshot
from agent_bridge_worker.redaction import SecretScanner
from agent_comms.adapters.event_log import JsonlEventLog
from agent_comms.models import Envelope, MessageKind, TaskEvent, TaskStatus

NOW = datetime( 2026, 11, 3, 15, 0, 0, tzinfo=timezone.utc )


def _envelope( n: int, status: TaskStatus, *, agent: str = "codex", sender: str = "chatgpt-bridge",
               workdir: str = "/w/agent_blocks", minutes_ago: int = 10, ran: int | None = None,
               error: str | None = None, task: str | None = None ) -> Envelope:
    made = NOW - timedelta( minutes=minutes_ago )
    started = made + timedelta( seconds=5 ) if status != TaskStatus.QUEUED else None
    ended = started + timedelta( seconds=ran ) if ( started and ran is not None ) else None
    context = { "workdir": workdir }
    if task != "":
        context[ "bridge_task_id" ] = task or f"t_{ n:024x}"
    return Envelope( id=f"e{ n }", kind=MessageKind.REQUEST, sender=sender, recipient=agent, task_type="inspect",
                     text="the prompt, which must never reach the snapshot", context=context,
                     correlation_id=f"e{ n }", attempts=0 if status == TaskStatus.QUEUED else 1, status=status,
                     error=error, created_at=made, enqueued_at=made, dequeued_at=started, completed_at=ended )


def _post( root: Path, envelope: Envelope, box: str ) -> None:
    folder = root / envelope.recipient / "inbox" / box
    folder.mkdir( parents=True, exist_ok=True )
    ( folder / f"{ envelope.id }.json" ).write_text( envelope.model_dump_json(), encoding="utf-8" )


def _workspaces( root: Path ) -> Path:
    path = root / "workspaces.json"
    path.write_text( json.dumps({ "workspaces": { "agent_blocks": { "path": "/w/agent_blocks" },
                                                  "letta_code": { "path": "/w/letta-code" }}}))
    return path


def test_rows_come_only_from_bridge_requests_and_open_work_leads() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path( tmp ) / "mailboxes"
        _post( root, _envelope( 1, TaskStatus.SUCCEEDED, minutes_ago=30, ran=20 ), "completed" )
        _post( root, _envelope( 2, TaskStatus.RUNNING, agent="codex-writer", workdir="/w/letta-code", minutes_ago=3 ), "in_process" )
        _post( root, _envelope( 3, TaskStatus.FAILED, minutes_ago=1, ran=4, error="codex exited 1" ), "failed" )
        _post( root, _envelope( 4, TaskStatus.SUCCEEDED, sender="toyota" ), "completed" )   # not the bridge's
        _post( root, _envelope( 5, TaskStatus.SUCCEEDED, task="" ), "completed" )           # no bridge task id
        ( root / "codex" / "inbox" / "completed" / "junk.json" ).write_text( "{not json" )
        rows = build_rows( root, _workspaces( Path( tmp )), NOW )
        assert [ r.id for r in rows ] == [ f"t_{ 2:024x}", f"t_{ 3:024x}", f"t_{ 1:024x}" ]
        running, failed, done = rows
        assert ( running.agent, running.workspace, running.status ) == ( "codex-writer", "letta_code", "running" )
        assert running.run_duration == "2m 55s" and running.queue_age == "5s"
        assert ( failed.status, failed.error, failed.run_duration ) == ( "failed", "codex exited 1", "4s" )
        assert ( done.workspace, done.attempts, done.operation ) == ( "agent_blocks", 1, "inspect" )


def test_a_queued_task_ages_and_has_no_run_time() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path( tmp ) / "mailboxes"
        _post( root, _envelope( 1, TaskStatus.QUEUED, minutes_ago=2 ), "new" )
        ( row, ) = build_rows( root, _workspaces( Path( tmp )), NOW )
        assert ( row.status, row.attempts, row.queue_age, row.run_duration ) == ( "queued", 0, "2m 00s", None )


def test_the_snapshot_never_carries_the_prompt_and_is_the_dashboard_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path( tmp ) / "mailboxes"
        _post( root, _envelope( 1, TaskStatus.SUCCEEDED, ran=3 ), "completed" )
        out = write_snapshot( build_rows( root, _workspaces( Path( tmp )), NOW ), Path( tmp ) / "c" / "snap.json" )
        text = out.read_text()
        data = json.loads( text )
        assert isinstance( data, list ) and all( isinstance( r, dict ) for r in data )
        assert set( data[ 0 ]) == { "id", "agent", "operation", "workspace", "status", "queue_age", "run_duration",
                                   "attempts", "last_update", "error", "queued_at", "started_at", "completed_at",
                                   "current", "events" }
        assert "prompt" not in text
        assert oct( out.stat().st_mode & 0o777 ) == "0o600"
        assert not list( out.parent.glob( ".worker_activity_*" ))


def test_an_error_that_looks_like_a_credential_is_withheld_and_long_ones_clipped() -> None:
    scanner = SecretScanner()
    assert "withheld" in safe_error( "auth failed: api_key=sk-abcdefghijklmnopqrstuvwxyz", scanner )
    clipped = safe_error( "x " * 500, scanner )
    assert len( clipped ) == 240 and clipped.endswith( "…" )
    assert safe_error( None, scanner ) is None


def test_durations_read_like_a_person_would_say_them() -> None:
    assert duration( NOW, NOW + timedelta( seconds=59 )) == "59s"
    assert duration( NOW, NOW + timedelta( seconds=184 )) == "3m 04s"
    assert duration( NOW, NOW + timedelta( hours=1, minutes=2 )) == "1h 02m"
    assert duration( None, NOW ) is None


def test_a_missing_mailbox_root_is_an_empty_ledger_not_a_crash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert build_rows( Path( tmp ) / "nope", Path( tmp ) / "nope.json", NOW ) == []


def test_a_row_lists_its_own_recorded_events_and_latest_step() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path( tmp ) / "mailboxes"
        _post( root, _envelope( 1, TaskStatus.RUNNING, agent="claude-sonnet", minutes_ago=2 ), "in_process" )
        _post( root, _envelope( 2, TaskStatus.SUCCEEDED, ran=3 ), "completed" )
        log = JsonlEventLog( Path( tmp ) / "events.jsonl" )
        at = NOW - timedelta( minutes=1 )
        log.notify( TaskEvent( name="task.claimed", task_id="e1", agent="claude-sonnet", at=at ))
        log.notify( TaskEvent( name="task.progress", task_id="e1", agent="claude-sonnet", at=at,
                               details={ "phase": "tool", "message": "Read README.md" }))
        log.notify( TaskEvent( name="task.progress", task_id="e1", agent="claude-sonnet", at=at,
                               details={ "phase": "tool", "message": "token sk-abcdefghijklmnopqrstuvwxyz" }))
        log.notify( TaskEvent( name="task.progress", task_id="other", agent="codex", at=at,
                               details={ "phase": "tool", "message": "not this task" }))
        running, done = build_rows( root, _workspaces( Path( tmp )), NOW )
        assert [ e[ "phase" ] for e in running.events ] == [ "claimed", "tool", "tool" ]
        assert running.events[ 1 ][ "message" ] == "Read README.md"
        assert "withheld" in running.events[ 2 ][ "message" ]
        assert running.current == running.events[ 2 ][ "message" ]
        assert running.started_at and running.completed_at is None and running.queued_at
        assert done.events == [] and done.current is None and done.completed_at


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( f"ok  { name }" )
    print( "all activity snapshot tests passed" )

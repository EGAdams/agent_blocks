"""Changing the poll settings, offline: fake service, fake state file, fake clock.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_settings_editor
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.poll_state_file import JsonPollStateFile
from agent_bridge_worker.adapters.systemd_user_service import parse_show
from agent_bridge_worker.settings_editor import PollSettingsEditor, WorkerStatus
from agent_bridge_worker.watch import PollState

NOW = 1_000_000.0
OLD = { "idle_seconds": 30, "burst_seconds": 5, "burst_window_seconds": 300, "slow_down_seconds": 3600,
        "error_base_seconds": 30, "error_max_seconds": 900, "active_hours": None }


class FakeService:
    def __init__( self, active: str = "active", pid: int = 100, started_at: float = NOW - 600 ) -> None:
        self.current  = WorkerStatus( active=active, pid=pid, started_at=started_at )
        self.restarts = 0

    def status( self ) -> WorkerStatus:
        return self.current

    def restart( self ) -> None:
        self.restarts += 1
        self.current = WorkerStatus( active="active", pid=self.current.pid + 1, started_at=NOW )


class FakeState:
    """Reports `phase` for whichever pid the service has now, like the real worker would."""

    def __init__( self, service: FakeService, phase: str = "waiting", follows: bool = True ) -> None:
        self._service, self.phase, self._follows = service, phase, follows
        self._pid = service.current.pid

    def read( self ) -> dict[ str, Any ] | None:
        pid = self._service.current.pid if self._follows else self._pid
        return { "phase": self.phase, "pid": pid }


class Clock:
    def __init__( self ) -> None:
        self.t = NOW

    def now( self ) -> float:
        return self.t

    def sleep( self, seconds: float ) -> None:
        self.t += seconds


def _editor( service: FakeService, state: FakeState | None = None ) -> tuple[ PollSettingsEditor, Path, Clock ]:
    folder = Path( tempfile.mkdtemp())
    path   = folder / "poll_settings.json"
    path.write_text( json.dumps( OLD ), encoding="utf-8" )
    clock  = Clock()
    return PollSettingsEditor( path, service, state or FakeState( service ), clock.now, clock.sleep ), path, clock


def test_good_settings_are_written_backed_up_and_the_worker_restarted() -> None:
    service = FakeService()
    editor, path, _ = _editor( service )
    result = editor.apply({ **OLD, "idle_seconds": 45, "active_hours": { "start_hour": 7, "end_hour": 24 }})
    assert result.ok, result.message
    written = json.loads( path.read_text())
    assert written[ "idle_seconds" ] == 45 and isinstance( written[ "idle_seconds" ], int )
    assert written[ "active_hours" ] == { "start_hour": 7, "end_hour": 24 }
    assert json.loads( Path( result.backup ).read_text()) == OLD
    assert service.restarts == 1 and result.worker_pid == 101


def test_the_worker_limits_are_the_limits() -> None:
    service = FakeService()
    editor, path, _ = _editor( service )
    result = editor.apply({ **OLD, "idle_seconds": 3, "active_hours": { "start_hour": 9, "end_hour": 9 }})
    assert not result.ok
    fields = { p.field for p in result.problems }
    assert fields == { "idle_seconds", "active_hours" }, result.problems
    assert json.loads( path.read_text()) == OLD and service.restarts == 0


def test_unknown_keys_are_refused() -> None:
    editor, _, _ = _editor( FakeService())
    result = editor.apply({ **OLD, "token": "x" })
    assert not result.ok and result.problems[ 0 ].field == "token"


def test_never_restarts_in_the_middle_of_a_task() -> None:
    service = FakeService()
    editor, path, _ = _editor( service, FakeState( service, phase="working" ))
    result = editor.apply({ **OLD, "idle_seconds": 60 })
    assert not result.ok and "task" in result.message
    assert json.loads( path.read_text()) == OLD and service.restarts == 0


def test_refuses_a_second_restart_straight_after_the_first() -> None:
    service = FakeService( started_at=NOW - 3 )
    editor, path, _ = _editor( service )
    result = editor.apply({ **OLD, "idle_seconds": 60 })
    assert not result.ok and "moment ago" in result.message
    assert json.loads( path.read_text()) == OLD


def test_a_stopped_worker_is_saved_for_and_left_stopped() -> None:
    service = FakeService( active="inactive", pid=0 )
    editor, path, _ = _editor( service )
    result = editor.apply({ **OLD, "idle_seconds": 60 })
    assert result.ok and "left stopped" in result.message
    assert json.loads( path.read_text())[ "idle_seconds" ] == 60 and service.restarts == 0


def test_says_so_when_the_new_worker_never_reports() -> None:
    service = FakeService()
    editor, path, clock = _editor( service, FakeState( service, follows=False ))
    result = editor.apply({ **OLD, "idle_seconds": 60 })
    assert not result.ok and "journalctl" in result.message
    assert json.loads( path.read_text())[ "idle_seconds" ] == 60      # saved, and the message says so
    assert clock.t >= NOW + 30


def test_state_file_reads_back_what_it_wrote() -> None:
    path  = Path( tempfile.mkdtemp()) / "state.json"
    store = JsonPollStateFile( path )
    assert store.read() is None
    store.changed( PollState( phase="waiting", outcome="idle", checks=1 ))
    assert store.read()[ "phase" ] == "waiting"
    path.write_text( "{half", encoding="utf-8" )
    assert store.read() is None


def test_parse_show() -> None:
    s = parse_show( "ActiveState=active\nMainPID=42\nActiveEnterTimestamp=@1761960000\n" )
    assert ( s.active, s.pid, s.started_at ) == ( "active", 42, 1761960000.0 )
    assert parse_show( "" ).active == "unknown"


if __name__ == "__main__":
    tests = [ value for name, value in sorted( globals().items()) if name.startswith( "test_" )]
    for test in tests:
        test()
        print( f"ok  { test.__name__ }" )
    print( f"{ len( tests )} passed" )

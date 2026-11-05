"""WorkerSupervisor offline: fake systemd, fixed clock, temp health file.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_supervisor
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.supervisor import JsonHealthFile, Limits, Observation, WorkerSupervisor, assess

NOW = 1_800_000_000.0
CMD = "/x/.venv/bin/python adws/adw_bridge_worker.py --watch --url https://b/api/agent-bridge/v1"


def _iso( t: float ) -> str:
    return datetime.fromtimestamp( t ).astimezone().isoformat()


def _obs( **kw ) -> Observation:
    base = dict( now=NOW, active_state="active", main_pid=42, exit_status=0, active_since=NOW - 3600, command=CMD,
                 poll_state={ "pid": 42, "phase": "waiting", "outcome": "idle", "next_check_at": _iso( NOW + 30 )},
                 last_output_at=NOW - 10 )
    base.update( kw )
    return Observation( **base )


def test_healthy_waiting_and_working() -> None:
    assert assess( _obs()).status == "healthy"
    working = { "pid": 42, "phase": "working" }
    assert assess( _obs( poll_state=working, last_output_at=NOW - 600 )).status == "healthy"


def test_stopped_and_wrong_process_restart() -> None:
    for o in ( _obs( active_state="inactive" ), _obs( active_state="failed", exit_status=1 ),
               _obs( main_pid=0, command="" ), _obs( command="/usr/bin/sleep 100" )):
        v = assess( o )
        assert v.status == "stopped" and v.restart, ( o, v )


def test_bad_token_and_hold_never_restart() -> None:
    v = assess( _obs( active_state="failed", exit_status=78 ))
    assert v.status == "unauthorised" and not v.restart
    v = assess( _obs( active_state="inactive", held=True ))
    assert v.status == "held" and not v.restart


def test_stalled_loop_and_silent_work_restart() -> None:
    late = { "pid": 42, "phase": "waiting", "next_check_at": _iso( NOW - 600 )}
    assert assess( _obs( poll_state=late )).restart
    silent = { "pid": 42, "phase": "working" }
    assert assess( _obs( poll_state=silent, last_output_at=NOW - 2000 )).status == "stalled"


def test_fresh_start_gets_grace_then_counts_as_stalled() -> None:
    other = { "pid": 7, "phase": "waiting" }
    assert assess( _obs( poll_state=other, active_since=NOW - 30 )).status == "starting"
    assert assess( _obs( poll_state=other, active_since=NOW - 600 )).restart


class FakeSystemd:
    def __init__( self, state="inactive", status="0" ) -> None:
        self.state, self.status, self.restarts = state, status, 0

    def show( self, unit ):
        return { "ActiveState": self.state, "MainPID": "0", "ExecMainStatus": self.status,
                 "ActiveEnterTimestampMonotonic": "0" }

    def restart( self, unit ):
        self.restarts += 1
        return True, ""

    def last_output_at( self, unit ):
        return None


def _sup( systemd, tmp, clock, limits=Limits()) -> WorkerSupervisor:
    lines = []
    sup = WorkerSupervisor( systemd, JsonHealthFile( Path( tmp ) / "health.json" ), lambda pid: ( "", None, False ),
                            clock=lambda: clock[ 0 ], monotonic=lambda: 0.0, say=lines.append, limits=limits )
    sup.lines = lines
    return sup


def test_a_stopped_worker_is_restarted_a_bounded_number_of_times_and_logged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        systemd, clock = FakeSystemd(), [ NOW ]
        sup = _sup( systemd, tmp, clock, Limits( max_restarts=2, window_seconds=3600 ))
        actions = []
        for _ in range( 4 ):
            actions.append( sup.run_once()[ "action" ])
            clock[ 0 ] += 60
        assert systemd.restarts == 2, actions
        assert actions[ :2 ] == [ "restarted", "restarted" ] and actions[ 2 ].startswith( "gave up" ), actions
        clock[ 0 ] += 3600
        assert sup.run_once()[ "action" ] == "restarted" and systemd.restarts == 3
        report = JsonHealthFile( Path( tmp ) / "health.json" ).load()
        assert report[ "status" ] == "stopped" and report[ "events" ] and sup.lines


def test_dry_run_and_exit_78_do_not_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        systemd = FakeSystemd()
        assert _sup( systemd, tmp, [ NOW ]).run_once( dry_run=True )[ "action" ].startswith( "would restart" )
        systemd78 = FakeSystemd( state="failed", status="78" )
        report = _sup( systemd78, tmp, [ NOW ]).run_once()
        assert report[ "status" ] == "unauthorised" and systemd.restarts == systemd78.restarts == 0


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( f"ok  { name }" )
    print( "all supervisor tests passed" )

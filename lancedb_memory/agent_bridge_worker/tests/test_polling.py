"""The poll schedule and loop, offline: fixed clocks, no network.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_polling
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from pydantic import ValidationError

from agent_bridge_worker.polling import (ActiveHours, ActiveHoursOnly, BurstAfterWork, ErrorBackoff, PollOutcome,
                                         PollSettings, WorkerNotAuthorised, build_schedule, classify, load_settings)
from agent_bridge_worker.ports import GatewayError
from agent_bridge_worker.adapters.poll_state_file import JsonPollStateFile
from agent_bridge_worker.watch import PollLoop, PollState

NOON = datetime( 2026, 10, 31, 12, 0, 0 )
SETTINGS_FILE = Path( __file__ ).resolve().parents[ 1 ] / "poll_settings.json"


def test_defaults_are_the_agreed_numbers() -> None:
    s = PollSettings()
    assert ( s.idle_seconds, s.burst_seconds, s.burst_window_seconds, s.slow_down_seconds ) == ( 30, 5, 300, 3600 )
    assert s.active_hours is None


def test_shipped_settings_preserve_the_configured_poll_rate_and_active_hours() -> None:
    settings = load_settings( SETTINGS_FILE )
    assert ( settings.idle_seconds, settings.burst_seconds, settings.burst_window_seconds ) == ( 60, 10, 300 )
    assert settings.active_hours == ActiveHours( start_hour=8, end_hour=23 )


def test_missing_settings_file_means_defaults() -> None:
    assert load_settings( Path( "/nonexistent/poll.json" )) == PollSettings()
    assert load_settings( None ) == PollSettings()


def test_settings_reject_typos_and_hammering() -> None:
    for bad in ({ "idle_secs": 30 }, { "idle_seconds": 1 }, { "burst_seconds": 0.5 }):
        try:
            PollSettings.model_validate( bad )
        except ValidationError:
            continue
        raise AssertionError( f"accepted { bad }" )


def test_settings_file_override() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path( tmp ) / "poll.json"
        path.write_text( json.dumps({ "idle_seconds": 60, "active_hours": { "start_hour": 7, "end_hour": 24 }}))
        s = load_settings( path )
    assert s.idle_seconds == 60 and s.active_hours == ActiveHours( start_hour=7, end_hour=24 )


def test_idle_cadence() -> None:
    sched = BurstAfterWork( 30, 5, 300 )
    assert sched.delay( PollOutcome.IDLE, NOON ) == 30


def test_burst_after_work_then_back_to_idle() -> None:
    sched = BurstAfterWork( 30, 5, 300 )
    assert sched.delay( PollOutcome.WORKED, NOON ) == 5
    assert sched.delay( PollOutcome.IDLE, NOON + timedelta( seconds=299 )) == 5
    assert sched.delay( PollOutcome.IDLE, NOON + timedelta( seconds=300 )) == 30


def test_slow_down_waits_an_hour_then_recovers() -> None:
    sched = ErrorBackoff( BurstAfterWork( 30, 5, 300 ), 3600, 30, 900 )
    assert sched.delay( PollOutcome.SLOW_DOWN, NOON ) == 3600
    assert sched.delay( PollOutcome.IDLE, NOON ) == 30


def test_errors_double_up_to_the_cap_and_reset() -> None:
    sched = ErrorBackoff( BurstAfterWork( 30, 5, 300 ), 3600, 30, 900 )
    waits = [ sched.delay( PollOutcome.ERROR, NOON ) for _ in range( 7 )]
    assert waits == [ 30, 60, 120, 240, 480, 900, 900 ], waits
    assert sched.delay( PollOutcome.IDLE, NOON ) == 30
    assert sched.delay( PollOutcome.ERROR, NOON ) == 30


def test_active_hours_sleep_until_seven() -> None:
    sched = ActiveHoursOnly( BurstAfterWork( 30, 5, 300 ), ActiveHours( start_hour=7, end_hour=24 ))
    assert sched.delay( PollOutcome.IDLE, NOON ) == 30
    one_am = datetime( 2026, 11, 1, 1, 0, 0 )
    assert sched.delay( PollOutcome.IDLE, one_am ) == 6 * 3600
    assert sched.delay( PollOutcome.IDLE, datetime( 2026, 11, 1, 6, 59, 30 )) == 30


def test_active_hours_closing_wraps_to_next_morning() -> None:
    sched = ActiveHoursOnly( BurstAfterWork( 30, 5, 300 ), ActiveHours( start_hour=7, end_hour=22 ))
    assert sched.delay( PollOutcome.IDLE, datetime( 2026, 10, 31, 23, 0, 0 )) == 8 * 3600


def test_active_hours_overnight_window() -> None:
    hours = ActiveHours( start_hour=22, end_hour=6 )
    assert hours.overnight
    assert sorted( h for h in range( 24 ) if hours.contains( h )) == [ 0, 1, 2, 3, 4, 5, 22, 23 ]
    sched = ActiveHoursOnly( BurstAfterWork( 30, 5, 300 ), hours )
    assert sched.delay( PollOutcome.IDLE, datetime( 2026, 11, 1, 23, 30, 0 )) == 30      # before midnight
    assert sched.delay( PollOutcome.IDLE, datetime( 2026, 11, 2, 3, 0, 0 )) == 30        # after midnight
    assert sched.delay( PollOutcome.IDLE, NOON ) == 10 * 3600                            # opens at 22:00 today
    assert sched.delay( PollOutcome.IDLE, datetime( 2026, 11, 1, 6, 0, 0 )) == 16 * 3600


def test_active_hours_reject_equal_hours() -> None:
    for start, end in [( 9, 9 ), ( 0, 24 )]:
        try:
            ActiveHours( start_hour=start, end_hour=end )
        except ValidationError:
            continue
        raise AssertionError( f"accepted { start } to { end }" )


def test_factory_stacks_decorators() -> None:
    plain = build_schedule( PollSettings())
    assert isinstance( plain, ErrorBackoff )
    hours = build_schedule( PollSettings( active_hours=ActiveHours()))
    assert isinstance( hours, ActiveHoursOnly )


def test_classify() -> None:
    assert classify( GatewayError( 429, "rate", "x" )) is PollOutcome.SLOW_DOWN
    assert classify( GatewayError( 503, "busy", "x" )) is PollOutcome.SLOW_DOWN
    assert classify( GatewayError( 0, "unreachable", "x" )) is PollOutcome.OFFLINE
    assert classify( GatewayError( 500, "server", "x" )) is PollOutcome.ERROR
    assert classify( RuntimeError( "boom" )) is PollOutcome.ERROR
    for status in ( 401, 403 ):
        try:
            classify( GatewayError( status, "unauthorized", "x" ))
        except WorkerNotAuthorised:
            continue
        raise AssertionError( f"{ status } did not stop the worker" )


class ScriptedWaiter:
    """Records each wait; stops after `limit` waits."""

    def __init__( self, limit: int ) -> None:
        self.waits: list[ float ] = []
        self._limit = limit

    @property
    def stopping( self ) -> bool:
        return len( self.waits ) >= self._limit

    def wait( self, seconds: float ) -> bool:
        self.waits.append( seconds )
        return self.stopping


def _loop( answers: list, limit: int, ran: list[ int ] | None = None,
           observers: list | None = None ) -> tuple[ PollLoop, ScriptedWaiter, list[ str ]]:
    queue = list( answers )

    def has_work() -> bool:
        item = queue.pop( 0 )
        if isinstance( item, Exception ):
            raise item
        return bool( item )

    def run_work() -> int:
        if ran is not None:
            ran.append( 1 )
        return 1

    waiter, said = ScriptedWaiter( limit ), []
    loop = PollLoop( has_work, run_work, build_schedule( PollSettings()), waiter, lambda: NOON, said.append,
                     observers or [])
    return loop, waiter, said


def test_loop_idle_checks_are_cheap_and_quiet() -> None:
    ran: list[ int ] = []
    loop, waiter, said = _loop([ False ] * 5, 5, ran )
    loop.run()
    assert waiter.waits == [ 30 ] * 5 and ran == []
    assert said == [ "idle; next check in 30s" ]            # one line for the change, not one per check


def test_loop_runs_work_then_bursts() -> None:
    ran: list[ int ] = []
    loop, waiter, said = _loop([ True, False, False ], 3, ran )
    loop.run()
    assert ran == [ 1 ] and waiter.waits == [ 5, 5, 5 ]


def test_loop_survives_errors_and_backs_off() -> None:
    loop, waiter, said = _loop([ GatewayError( 0, "unreachable", "dns" ), GatewayError( 429, "rate", "slow" ), False ], 3 )
    loop.run()
    assert waiter.waits == [ 30, 3600, 30 ], waiter.waits
    assert any( "unreachable" in line for line in said )


def test_loop_stops_on_rejected_token() -> None:
    loop, waiter, _ = _loop([ GatewayError( 401, "unauthorized", "bad token" )], 5 )
    try:
        loop.run()
    except WorkerNotAuthorised:
        assert waiter.waits == []
        return
    raise AssertionError( "kept polling with a rejected token" )


class RecordingObserver:
    def __init__( self ) -> None:
        self.states: list[ PollState ] = []

    def changed( self, state: PollState ) -> None:
        self.states.append( state )


class BrokenObserver:
    def changed( self, state: PollState ) -> None:
        raise OSError( "disk full" )


def test_loop_tells_observers_when_the_next_check_is() -> None:
    seen = RecordingObserver()
    loop, _, _ = _loop([ False, True ], 2, observers=[ seen ])
    loop.run()
    phases = [ state.phase for state in seen.states ]
    assert phases == [ "waiting", "working", "waiting", "stopped" ], phases
    first = seen.states[ 0 ]
    assert first.delay_seconds == 30 and first.next_check_at == NOON + timedelta( seconds=30 )
    assert seen.states[ 2 ].outcome is PollOutcome.WORKED and seen.states[ 2 ].delay_seconds == 5
    assert seen.states[ -1 ].checks == 2


def test_broken_observer_never_stops_the_worker() -> None:
    loop, waiter, said = _loop([ False, False ], 2, observers=[ BrokenObserver()])
    loop.run()
    assert waiter.waits == [ 30, 30 ]
    assert any( "observer failed" in line for line in said )


def test_state_file_is_whole_json_with_no_secrets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path( tmp ) / "deep" / "poll_state.json"
        JsonPollStateFile( path ).changed( PollState( phase="waiting", outcome=PollOutcome.IDLE, checked_at=NOON,
                                                      delay_seconds=30, next_check_at=NOON, checks=3 ))
        data = json.loads( path.read_text())
        assert data[ "phase" ] == "waiting" and data[ "checks" ] == 3 and data[ "pid" ]
        assert set( data ) == { "phase", "outcome", "checked_at", "delay_seconds", "next_check_at", "checks", "pid" }
        assert not list( path.parent.glob( "*.tmp" ))


def test_offline_waits_the_base_time_and_never_doubles() -> None:
    sched = ErrorBackoff( BurstAfterWork( 30, 5, 300 ), 3600, 60, 900 )
    assert [ sched.delay( PollOutcome.OFFLINE, NOON ) for _ in range( 6 )] == [ 60 ] * 6
    assert sched.delay( PollOutcome.ERROR, NOON ) == 60 and sched.delay( PollOutcome.ERROR, NOON ) == 120


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( f"ok  { name }" )
    print( "all polling tests passed" )

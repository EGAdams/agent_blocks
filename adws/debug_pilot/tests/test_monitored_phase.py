"""MonitoredPhase with fakes: no SSSF session, no sqlite, no real sleeping."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from debug_pilot.faults import NoFaults, PilotFault, PlannedFaults
from debug_pilot.interfaces import FaultPlan
from debug_pilot.monitored_phase import MonitoredPhase
from debug_pilot.steps import TORTURE, LookupStep, PrepareStep


class FakeEvents:
    def __init__( self ):
        self.rows = []

    def emit( self, kind, phase_id="", **fields ):
        self.rows.append(( kind, phase_id, fields ))

    def kinds( self ):
        return [ row[ 0 ] for row in self.rows ]


class FakeSleeper:
    def __init__( self ):
        self.slept = []

    def sleep( self, seconds ):
        self.slept.append( seconds )


HANDLE = SimpleNamespace( phase=SimpleNamespace( phase_id="run1_01_prepare" ))


def build( step, faults=None ):
    events = FakeEvents()
    return MonitoredPhase( step, "run1", events, faults or NoFaults()), events


def test_construction_announces_instance_and_purpose_once():
    _, events = build( PrepareStep( "prepare", FakeSleeper(), 0 ))
    assert events.kinds() == [ "constructed", "purpose" ]
    assert events.rows[ 0 ][ 2 ][ "instance_id" ] == "run1:prepare"
    assert events.rows[ 1 ][ 2 ][ "text" ].startswith( "Decide which items" )


def test_a_clean_run_binds_works_and_cleans_up_inside_the_phase():
    step = PrepareStep( "prepare", FakeSleeper(), 0 )
    phase, events = build( step )
    phase.act( HANDLE )
    assert events.kinds() == [ "constructed", "purpose", "bound", "progress", "message", "cleanup" ]
    assert events.rows[ -1 ][ 1 ] == "run1_01_prepare"
    assert events.rows[ -1 ][ 2 ][ "result" ] == "ok"
    assert step.disposed
    assert events.rows[ 4 ][ 2 ][ "target_instance" ] == "run1:report"


def test_an_exception_still_cleans_up_and_still_propagates():
    step = PrepareStep( "prepare", FakeSleeper(), 0 )
    phase, events = build( step, PlannedFaults( FaultPlan( fail_at="prepare" ), FakeSleeper()))
    with pytest.raises( PilotFault ):
        phase.act( HANDLE )
    assert events.kinds()[ -1 ] == "cleanup"
    assert step.disposed


def test_cleanup_happens_exactly_once():
    phase, events = build( PrepareStep( "prepare", FakeSleeper(), 0 ))
    phase.act( HANDLE )
    phase.close()
    phase.close()
    assert events.kinds().count( "cleanup" ) == 1


def test_a_step_that_never_ran_is_closed_and_says_so():
    phase, events = build( PrepareStep( "prepare", FakeSleeper(), 0 ))
    phase.close()
    assert events.rows[ -1 ][ 1 ] == ""
    assert events.rows[ -1 ][ 2 ][ "note" ] == "never executed"


def test_a_failing_dispose_is_reported_not_raised():
    step = PrepareStep( "prepare", FakeSleeper(), 0 )
    step.dispose = lambda: ( _ for _ in ()).throw( OSError( "disk gone" ))
    phase, events = build( step )
    phase.act( HANDLE )
    assert events.rows[ -1 ][ 2 ][ "result" ] == "error"
    assert events.rows[ -1 ][ 2 ][ "error" ] == "disk gone"


def test_two_lookups_share_a_name_and_keep_separate_identities():
    first, a  = build( LookupStep( "lookup-1", FakeSleeper(), 0, "alpha", "one" ))
    second, b = build( LookupStep( "lookup-2", FakeSleeper(), 0, "beta", TORTURE ))
    assert first.params.name == second.params.name == "lookup"
    assert a.rows[ 0 ][ 2 ][ "instance_id" ] != b.rows[ 0 ][ 2 ][ "instance_id" ]
    second.act( HANDLE )
    call, result = b.rows[ 3 ], b.rows[ 4 ]
    assert call[ 2 ][ "call_id" ] == result[ 2 ][ "call_id" ]
    assert result[ 2 ][ "result" ] == TORTURE


def test_a_stall_is_silent_then_reports_recovery():
    sleeper = FakeSleeper()
    phase, events = build( PrepareStep( "prepare", FakeSleeper(), 0 ),
                           PlannedFaults( FaultPlan( stall_at="prepare", stall_seconds=20 ), sleeper ))
    phase.act( HANDLE )
    assert sleeper.slept == [ 20 ]
    assert events.kinds()[ 2:4 ] == [ "bound", "progress" ]

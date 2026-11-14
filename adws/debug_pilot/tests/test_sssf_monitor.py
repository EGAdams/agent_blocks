"""Existing phases keep their behavior when the debugger observes them."""
from types import SimpleNamespace
import sys
from pathlib import Path

import pytest

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from adw_modules.data_types import PhaseParams
from adw_modules.monitored_phase import MonitoredSssfPhase


class Events:
    def __init__( self ):
        self.rows = []

    def emit( self, kind, phase_id="", **fields ):
        self.rows.append(( kind, phase_id, fields ))


class Phase:
    params = PhaseParams( name="classify", kind="code", owner="scala", description="Classify the page" )

    def __init__( self, result="skipped", error=None ):
        self.result, self.error = result, error
        self.handles = []

    def act( self, handle ):
        self.handles.append( handle )
        if self.error:
            raise self.error
        return self.result


HANDLE = SimpleNamespace( phase=SimpleNamespace( phase_id="run_04_classify" ))


def test_existing_handle_params_and_skipped_result_are_preserved():
    original, events = Phase(), Events()
    phase = MonitoredSssfPhase( original, "run", "classify", events )
    assert phase.params is original.params
    assert phase.act( HANDLE ) == "skipped"
    phase.close()
    assert original.handles == [ HANDLE ]
    assert [ r[ 0 ] for r in events.rows ] == [ "constructed", "purpose", "bound", "progress", "cleanup" ]
    assert events.rows[ -2 ][ 2 ][ "text" ] == "skipped"
    assert all( r[ 2 ][ "instance_id" ] == "run:classify" for r in events.rows )
    assert all( r[ 1 ] == HANDLE.phase.phase_id for r in events.rows[ 2: ])


def test_failure_propagates_and_cleanup_does_not_invent_a_phase_outcome():
    error, events = OSError( "OCR failed" ), Events()
    phase = MonitoredSssfPhase( Phase( error=error ), "run", "classify", events )
    with pytest.raises( OSError ) as raised:
        phase.act( HANDLE )
    phase.close()
    assert raised.value is error
    assert [ r[ 0 ] for r in events.rows ] == [ "constructed", "purpose", "bound", "cleanup" ]
    assert events.rows[ -1 ][ 2 ][ "note" ] == "monitor scope closed"


def test_unreached_phase_is_closed_once_without_starting_it():
    original, events = Phase(), Events()
    phase = MonitoredSssfPhase( original, "run", "classify", events )
    phase.close()
    phase.close()
    assert original.handles == []
    assert [ r[ 0 ] for r in events.rows ] == [ "constructed", "purpose", "cleanup" ]
    assert events.rows[ -1 ][ 1 ] == ""
    assert "never executed" in events.rows[ -1 ][ 2 ][ "note" ]

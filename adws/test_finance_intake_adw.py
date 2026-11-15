"""The finance intake ADW on fakes: no database, no vision, no dashboard.

Run where the SSSF template is installed (the live box):

    cd /home/adamsl/agent_blocks/adws && /home/adamsl/rol_finances/.venv/bin/python \\
        -m pytest test_finance_intake_adw.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( HERE ))

adw = pytest.importorskip( "adw_interface_finance_intake" )

from finance_app.intake.composition import IntakePorts, build_facade              # noqa: E402
from finance_app.intake.durable import SqliteReviewQueue, SqliteRunJournal        # noqa: E402
from finance_app.tests import fakes                                               # noqa: E402

PHASES = [ "normalize", "classify", "extract", "resolve", "judge", "validate",
           "commit", "project", "verify", "publish" ]


def isolated_config( tmp: Path ) -> Path:
    config = yaml.safe_load(( HERE / "adw_sssf_config" / "sssf.config.yaml" ).read_text())
    config[ "observability" ][ "db" ] = str( tmp / "sssf.db" )
    config[ "defaults" ][ "data_dir" ] = str( tmp / "adw_data" )
    path = tmp / "sssf.yaml"
    path.write_text( yaml.safe_dump( config ))
    return path


def ports( tmp: Path, **over ) -> IntakePorts:
    db = str( tmp / "intake.sqlite3" )
    base = IntakePorts(
        classifier=fakes.FakeClassifier(), extractor=fakes.FakeExtractor(),
        vendors=fakes.FakeVendors(), duplicates=fakes.FakeDuplicates(),
        categories=fakes.FakeCategories(), receipts=fakes.FakeReceiptStore(),
        statements=fakes.FakeStatementStore(), projections=fakes.FakeProjections(),
        reviews=SqliteReviewQueue( db ), journal=SqliteRunJournal( db ),
        publisher=fakes.FakePublisher(), judgment=fakes.FakeGateway())
    return replace( base, **over )


def run_adw( tmp: Path, p: IntakePorts ):
    scan = tmp / "scan_freezer_1.jpg"
    scan.write_bytes( b"\xff\xd8 receipt" )
    out = tmp / "result.json"
    app = adw.build_application( facade_for=lambda request: build_facade( p ))
    code = app.run([ "test", "--scan", str( scan ), "--source", "Freezer Scanner",
                     "--result-out", str( out ), "--config", str( isolated_config( tmp ))])
    with sqlite3.connect( tmp / "sssf.db" ) as conn:
        rows = conn.execute( "SELECT type, payload_json FROM events ORDER BY rowid" ).fetchall()
    events = [( kind, json.loads( payload )) for kind, payload in rows ]
    return code, json.loads( out.read_text()), events


def debug( events, kind ):
    return [ p[ "adw_debug" ] for _, p in events
             if p.get( "adw_debug", {}).get( "kind" ) == kind ]


def test_a_resolved_receipt_runs_ten_observed_phases_and_no_model( tmp_path ):
    p = ports( tmp_path )
    code, result, events = run_adw( tmp_path, p )
    assert code == 0 and result[ "outcome" ] == "stored" and result[ "judgment_calls" ] == 0
    assert p.judgment.requests == [] and len( p.publisher.results ) == 1
    assert [ d[ "phase_name" ] for d in debug( events, "constructed" )] == PHASES
    assert len( debug( events, "purpose" )) == 10 and all( d[ "text" ] for d in debug( events, "purpose" ))
    assert len( debug( events, "bound" )) == 10 and len( debug( events, "cleanup" )) == 10
    assert [ p_[ "status" ] for kind, p_ in events if kind == "phase_end" ] == [ "success" ] * 10


def test_the_adw_run_id_is_the_finance_run_id( tmp_path ):
    _, result, events = run_adw( tmp_path, ports( tmp_path ))
    instance = debug( events, "constructed" )[ 0 ][ "instance_id" ]
    assert instance == f"{ result[ 'run_id' ] }:normalize"


def test_a_crashed_phase_is_red_and_publish_still_runs( tmp_path ):
    class Boom:
        def classify( self, source ):
            raise RuntimeError( "vision down" )
    p = ports( tmp_path, classifier=Boom())
    code, result, events = run_adw( tmp_path, p )
    statuses = [ p_[ "status" ] for kind, p_ in events if kind == "phase_end" ]
    assert code != 0 and result[ "outcome" ] == "failed"
    assert statuses[ 1 ] == "fail" and statuses.count( "fail" ) == 1 and len( statuses ) == 10
    assert len( p.publisher.results ) == 1 and p.receipts.calls == 0


def test_waiting_on_a_person_is_not_a_failed_run( tmp_path ):
    from finance_app.intake.contracts import DuplicateFinding, DuplicateKind
    p = ports( tmp_path, judgment=None, duplicates=fakes.FakeDuplicates(
        DuplicateFinding( kind=DuplicateKind.POSSIBLE, expense_ids=( 2201, ))))
    code, result, _ = run_adw( tmp_path, p )
    assert code == 0 and result[ "outcome" ] == "needs_human_review" and result[ "review_id" ]
    assert p.receipts.calls == 0

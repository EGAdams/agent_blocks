"""Status, cancel and discovery: none of them may ever print."""
from __future__ import annotations

import json

import pytest

from fakes import OFFICEJET, FakeBackend, Rig
from printer_agent.errors import RequestRefused
from printer_agent.models import ( JobState, PrintDocument, PrintLimits, PrintRequest, PrintStatus, RenderedDocument,
                                  RenderedPage )

QUEUED = PrintStatus( state=JobState.SUBMITTED_TO_SPOOLER, in_queue=True, flags=[ "Printing" ])


def _submitted( rig: Rig, job_id: str ) -> None:
    rig.workflow.run( job_id, json.dumps({ "kind": "text", "text": "hi" }))
    assert rig.registry.get( job_id ).state is JobState.SUBMITTED_TO_SPOOLER


def stuck_rig( tmp_path ) -> Rig:
    return Rig( tmp_path, backend=FakeBackend( statuses=[ QUEUED ]), limits=PrintLimits( monitor_seconds=0 ))


def test_status_never_prints( tmp_path ):
    rig = stuck_rig( tmp_path )
    _submitted( rig, "t_status0001" )
    rig.backend.statuses = [ PrintStatus( state=JobState.COMPLETED_PER_SPOOLER, in_queue=False, detail="released" )]
    rig.backend.status_calls = 0
    out = rig.control.status( "t_status0001" )
    assert out.state is JobState.COMPLETED_PER_SPOOLER and out.physical_output_verified is False
    assert len( rig.backend.submitted ) == 1


def test_status_of_unknown_or_malformed_job( tmp_path ):
    rig = Rig( tmp_path )
    with pytest.raises( RequestRefused ):
        rig.control.status( "t_nosuchjob1" )
    with pytest.raises( RequestRefused ):
        rig.control.status( "../../x" )
    assert rig.backend.submitted == []


def test_cancel_before_submission_wins( tmp_path ):
    rig = Rig( tmp_path )
    rig.workflow.receive( "t_cancel0001", json.dumps({ "kind": "text", "text": "hi" }))
    out = rig.control.cancel( "t_cancel0001" )
    assert out.state is JobState.CANCELED
    # the run that owned it now loses the compare-and-set at submit, and sends nothing
    document = PrintDocument( kind="text", title="t", data=b"hi", sha256="0" * 64, source="inline" )
    record = rig.workflow.submit( "t_cancel0001", OFFICEJET, document, RenderedDocument( pages=[ RenderedPage( png=b"x" )]),
                                  PrintRequest( kind="text", text="hi" ))
    assert record.state is JobState.CANCELED
    assert rig.backend.submitted == []


def test_cancel_in_the_spooler( tmp_path ):
    rig = stuck_rig( tmp_path )
    _submitted( rig, "t_cancel0002" )
    out = rig.control.cancel( "t_cancel0002" )
    assert out.state is JobState.CANCELED and rig.backend.canceled == [ "t_cancel0002" ]
    assert len( rig.backend.submitted ) == 1


def test_cancel_a_finished_job_changes_nothing( tmp_path ):
    rig = Rig( tmp_path )
    rig.workflow.run( "t_cancel0003", json.dumps({ "kind": "text", "text": "hi" }))
    out = rig.control.cancel( "t_cancel0003" )
    assert out.state is JobState.COMPLETED_PER_SPOOLER and "nothing to cancel" in out.detail
    assert rig.backend.canceled == []


def test_discover_lists_printers_default_documents_and_jobs( tmp_path ):
    rig = Rig( tmp_path )
    ( rig.outbox / "a.txt" ).write_text( "hello" )
    ( rig.outbox / ".hidden.txt" ).write_text( "no" )
    ( rig.outbox / "evil.txt" ).symlink_to( "/etc/hostname" )
    ( rig.outbox / "sub" ).mkdir()
    rig.workflow.run( "t_disc000001", json.dumps({ "kind": "text", "text": "hi" }))
    found = rig.control.discover()
    assert found.default == "hpi297bea_hp_officejet_8120e_series"
    assert [ d.name for d in found.documents ] == [ "a.txt" ]
    assert [ r.job_id for r in found.recent ] == [ "t_disc000001" ]
    assert len( rig.backend.submitted ) == 1

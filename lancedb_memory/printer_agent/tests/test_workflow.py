"""PrintWorkflow with the hardware mocked: every spec case that ends in a print, or in not printing."""
from __future__ import annotations

import json

import pytest

from fakes import DESKJET, FakeBackend, FakeDiscovery, FakeRenderer, Rig, raise_spooler
from printer_agent.adapters.print_lock import FlockPrintLock
from printer_agent.errors import PrinterBusy, SpoolerRefused
from printer_agent.models import JobState, PrintLimits, PrintStatus


def text( body: str = "hello", **extra: object ) -> str:
    return json.dumps({ "kind": "text", "title": "Note", "text": body, **extra })


def test_text_job_prints_once_on_the_default_printer( tmp_path ):
    rig = Rig( tmp_path )
    out = rig.workflow.run( "t_text000001", text())
    assert out.state is JobState.COMPLETED_PER_SPOOLER
    assert out.physical_output_verified is False
    assert out.printer_id == "hpi297bea_hp_officejet_8120e_series"
    assert len( rig.backend.submitted ) == 1
    assert rig.backend.submitted[ 0 ].options.color == "monochrome"
    assert rig.backend.submitted[ 0 ].options.paper == "letter"
    assert rig.backend.submitted[ 0 ].options.orientation == "portrait"
    assert out.submitted_at is not None


def test_named_printer_and_copies( tmp_path ):
    rig = Rig( tmp_path )
    out = rig.workflow.run( "t_named00001", text( printer_id=DESKJET.printer_id, options={ "copies": 3 }))
    assert out.printer == DESKJET.name and out.copies == 3
    assert rig.backend.submitted[ 0 ].options.copies == 3


def test_invalid_printer_is_refused_and_nothing_sent( tmp_path ):
    rig = Rig( tmp_path )
    out = rig.workflow.run( "t_badprn0001", text( printer_id="microsoft_print_to_pdf" ))
    assert out.state is JobState.FAILED and "unknown printer" in out.detail
    assert rig.backend.submitted == []


def test_no_default_printer_is_refused( tmp_path ):
    rig = Rig( tmp_path, discovery=FakeDiscovery([ DESKJET ]))
    out = rig.workflow.run( "t_nodflt0001", text())
    assert out.state is JobState.FAILED and "no default printer" in out.detail
    assert rig.backend.submitted == []


def test_missing_document_is_refused( tmp_path ):
    rig = Rig( tmp_path )
    out = rig.workflow.run( "t_missing001", json.dumps({ "kind": "document", "document_id": "doc_0123456789abcdef" }))
    assert out.state is JobState.FAILED and "no document doc_0123456789abcdef" in out.detail
    assert rig.backend.submitted == []


def test_outbox_document_prints_by_id( tmp_path ):
    rig = Rig( tmp_path )
    ( rig.outbox / "letter.pdf" ).write_bytes( b"%PDF-1.4 fake" )
    doc = rig.catalog.list_documents()[ 0 ]
    out = rig.workflow.run( "t_pdfdoc0001", json.dumps({ "kind": "document", "document_id": doc.document_id }))
    assert out.state is JobState.COMPLETED_PER_SPOOLER
    assert rig.renderer.seen[ 0 ].kind == "pdf" and rig.renderer.seen[ 0 ].source == doc.document_id
    assert out.sha256 == doc.sha256


@pytest.mark.parametrize( "prompt", [
    "not json",
    json.dumps({ "kind": "text", "text": "x", "path": "/etc/passwd" }),                    # no extra fields
    json.dumps({ "kind": "document", "document_id": "../../etc/passwd" }),                 # ids, never paths
    json.dumps({ "kind": "text", "text": "x", "printer_id": "HP; rm -rf /" }),
    json.dumps({ "kind": "text", "text": "x", "options": { "copies": 11 }}),
    json.dumps({ "kind": "text", "text": "x", "options": { "color": "color" }}),
    json.dumps({ "kind": "text", "text": "x", "options": { "page_range": "1;2" }}),
    json.dumps({ "kind": "text", "text": "y" * 6001 }),
    json.dumps({ "kind": "text", "text": "   " }),
    json.dumps({ "kind": "test_page", "text": "smuggled" }),
    json.dumps({ "kind": "shell", "text": "x" }),
])
def test_bad_requests_are_refused_and_never_sent( tmp_path, prompt ):
    rig = Rig( tmp_path )
    out = rig.workflow.run( "t_refused001", prompt )
    assert out.state is JobState.FAILED
    assert rig.backend.submitted == []
    assert "y" * 50 not in out.detail and "smuggled" not in out.detail       # the text is never echoed


def test_duplicate_job_id_never_prints_twice( tmp_path ):
    rig = Rig( tmp_path )
    first  = rig.workflow.run( "t_dupe000001", text())
    second = rig.workflow.run( "t_dupe000001", text())
    assert len( rig.backend.submitted ) == 1
    assert second.state is first.state


def test_spooler_refusal_is_failed_not_ambiguous( tmp_path ):
    rig = Rig( tmp_path, backend=FakeBackend( raises=SpoolerRefused( "printer_not_found" )))
    out = rig.workflow.run( "t_refuse0001", text())
    assert out.state is JobState.FAILED and "refused" in out.detail


def test_spooler_failure_is_ambiguous_and_never_reprinted( tmp_path ):
    rig = Rig( tmp_path, backend=FakeBackend( raises=raise_spooler()))
    out = rig.workflow.run( "t_ambig00001", text())
    assert out.state is JobState.AMBIGUOUS and "will not be reprinted" in out.detail
    again = rig.workflow.run( "t_ambig00001", text())
    assert again.state is JobState.AMBIGUOUS
    assert len( rig.backend.submitted ) == 1


def test_crash_while_submitting_becomes_ambiguous( tmp_path ):
    rig = Rig( tmp_path )
    record, request = rig.workflow.receive( "t_crash00001", text())
    rig.registry.move( "t_crash00001", JobState.QUEUED )
    rig.registry.move( "t_crash00001", JobState.SUBMITTING )       # ...and the process died here
    out = rig.workflow.run( "t_other00001", text())                 # the next run recovers it
    assert out.state is JobState.COMPLETED_PER_SPOOLER
    crashed = rig.registry.get( "t_crash00001" )
    assert crashed.state is JobState.AMBIGUOUS
    assert [ j.job_id for j in rig.backend.submitted ] == [ "t_other00001" ]


def test_monitor_times_out_leaving_it_submitted( tmp_path ):
    stuck = PrintStatus( state=JobState.SUBMITTED_TO_SPOOLER, in_queue=True, flags=[ "Printing" ], detail="in the queue" )
    rig = Rig( tmp_path, backend=FakeBackend( statuses=[ stuck ]), limits=PrintLimits( monitor_seconds=30, poll_seconds=3 ))
    out = rig.workflow.run( "t_slow000001", text())
    assert out.state is JobState.SUBMITTED_TO_SPOOLER and "after 30 s" in out.detail
    assert 1 <= rig.backend.status_calls <= 11


def test_monitor_polls_until_released( tmp_path ):
    statuses = [ PrintStatus( state=JobState.SUBMITTED_TO_SPOOLER, in_queue=True, flags=[ "Spooling" ]),
                 PrintStatus( state=JobState.SUBMITTED_TO_SPOOLER, in_queue=True, flags=[ "Printing" ], pages=2 ),
                 PrintStatus( state=JobState.COMPLETED_PER_SPOOLER, in_queue=False, detail="released; not verified" )]
    rig = Rig( tmp_path, backend=FakeBackend( statuses=statuses ), renderer=FakeRenderer( pages=2 ))
    out = rig.workflow.run( "t_poll000001", text())
    assert rig.backend.status_calls == 3
    assert out.state is JobState.COMPLETED_PER_SPOOLER and out.pages == 2


def test_rate_limit_refuses_before_sending( tmp_path ):
    rig = Rig( tmp_path, limits=PrintLimits( max_jobs_per_hour=2 ))
    for n in range( 2 ):
        rig.workflow.run( f"t_rate00000{ n }", text())
    out = rig.workflow.run( "t_rate000009", text())
    assert out.state is JobState.FAILED and "per hour" in out.detail
    assert len( rig.backend.submitted ) == 2


def test_one_job_at_a_time( tmp_path ):
    rig = Rig( tmp_path )
    holder = FlockPrintLock( tmp_path / "print.lock" )
    holder.acquire()
    try:
        with pytest.raises( PrinterBusy ):
            rig.workflow.run( "t_busy000001", text())
    finally:
        holder.release()
    assert rig.backend.submitted == []


def test_test_page_has_fixed_content_and_a_timestamp( tmp_path ):
    rig = Rig( tmp_path )
    out = rig.workflow.run( "t_testpg0001", json.dumps({ "kind": "test_page" }))
    assert out.state is JobState.COMPLETED_PER_SPOOLER
    page = rig.renderer.seen[ 0 ].data.decode( "utf-8" )
    assert rig.renderer.seen[ 0 ].title == "STAR TREK BRIDGE — PRINT TEST"
    assert "ChatGPT → StarTrekBridge → Printer Agent → Local Printer" in page
    assert "Print test successful." in page and "2026" in page

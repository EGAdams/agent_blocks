"""Small pure helpers shared by the workflows: request parsing, safe error text, reports, the test page."""
from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from .errors import RequestRefused
from .models import JobRecord, PrintReport, PrintRequest

TEST_PAGE_TITLE = "STAR TREK BRIDGE — PRINT TEST"


def explain( exc: ValidationError ) -> str:
    """Which fields were wrong and why -- never the values, which may be document text."""
    parts = [ f"{ '.'.join( str( p ) for p in err[ 'loc' ]) or 'request' }: { err[ 'msg' ]}" for err in exc.errors()[ :5 ]]
    return "; ".join( parts )


def parse_request( prompt: str ) -> PrintRequest:
    try:
        request = PrintRequest.model_validate_json( prompt )
    except ValidationError as exc:
        raise RequestRefused( f"the print request is not valid: { explain( exc )}" ) from None
    if request.kind == "text" and not ( request.text or "" ).strip():
        raise RequestRefused( "a text print needs some text" )
    if request.kind == "text" and request.document_id is not None:
        raise RequestRefused( "a text print takes text, not a document_id" )
    if request.kind == "document" and ( request.document_id is None or request.text is not None ):
        raise RequestRefused( "a document print takes a document_id from printer_list_documents, and no text" )
    if request.kind == "test_page" and ( request.text is not None or request.document_id is not None ):
        raise RequestRefused( "the test page has fixed content; send no text or document_id" )
    return request


def test_page_text( at: datetime ) -> str:
    return "\n".join([
        "ChatGPT → StarTrekBridge → Printer Agent → Local Printer",
        "",
        f"Submitted: { at.strftime( '%A, %B %d, %Y at %I:%M:%S %p %Z' ).strip()}",
        "",
        "Print test successful.",
    ])


def report( record: JobRecord ) -> PrintReport:
    return PrintReport( job_id=record.job_id, state=record.state, printer_id=record.printer_id, printer=record.printer,
                        title=record.title, pages=record.pages, copies=record.copies, sha256=record.sha256,
                        spooler_id=record.spooler_id, detail=record.detail, physical_output_verified=False,
                        submitted_at=record.submitted_at )

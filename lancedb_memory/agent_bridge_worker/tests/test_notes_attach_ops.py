"""The notes review_attach_pdf / attachments / attachment / detach_pdf operations offline: the upload is fetched
under the task's lease, checked against what the gateway described, handed to the ADW as a private temp file that is
always deleted, and the ADW's answer comes back.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_notes_attach_ops
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.notes_review_process import UvNotesReviewCommands
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.notes_agent import NotesAgent
from agent_bridge_worker.notes_ops.commands import default_registry
from agent_bridge_worker.ports import GatewayError
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_bridge_worker.tests.test_notes_ops import Dashboard, NoStore

NOTE   = "2026-11-03_draft-personal-representative-s-deed-of-distribution.md"
PDF    = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\ntrailer << >>\n%%EOF\n"
SHA    = hashlib.sha256( PDF ).hexdigest()
UPLOAD = "u_" + "0" * 24
ATTACH = "a-0123456789ab"


class FakeReview:
    """Answers like the ADW, and records what the PDF file held while it ran (and its mode)."""

    def __init__( self ) -> None:
        self.calls: list[ tuple ] = []
        self.seen: list[ tuple ] = []

    def run( self, operation, flags, switches, timeout, beat, stoppable ):
        self.calls.append(( operation, dict( flags ), stoppable ))
        if "pdf-file" in flags:
            path = Path( flags[ "pdf-file" ])
            self.seen.append(( path, path.read_bytes(), stat.S_IMODE( os.stat( path ).st_mode )))
        return { "ok": True, "line": f"Attached Latest Printable Deed PDF ({ ATTACH }) to { NOTE }.",
                 "data": { "note": NOTE, "attachment_id": ATTACH, "note_unchanged": True, "verified": True,
                           "attachment": { "id": ATTACH, "view_url": "https://x/api/notes/attachment-file" },
                           "adw_id": "abcd1234" }}


class FakeUploads:
    def __init__( self, data: bytes = PDF, upload_id: str = UPLOAD, fail: bool = False ) -> None:
        self.data, self.upload_id, self.fail = data, upload_id, fail
        self.tasks: list[ ClaimedTask ] = []

    def fetch_upload( self, task: ClaimedTask ) -> dict:
        self.tasks.append( task )
        if self.fail:
            raise GatewayError( 409, "lease_lost", "the lease is not held" )
        return { "upload_id": self.upload_id, "sha256": hashlib.sha256( self.data ).hexdigest(),
                 "bytes": len( self.data ), "name": "deed.pdf", "pdf_base64": base64.b64encode( self.data ).decode()}


def ask( payload: dict, operation: str, review: FakeReview, uploads: FakeUploads | None, workdir: Path ):
    t = ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent="notes",
                     workspace="notes", operation=operation,
                     approval_mode="read_only" if operation == "inspect" else "workspace_write", prompt=json.dumps( payload ))
    dash = Dashboard()
    registry = default_registry( review=review, uploads=uploads, workdir=workdir )
    return NotesAgent( NoStore(), dash, dash, commands=registry ).run( t, Path( "/nonexistent" ), lambda: "continue" )


def attach_payload( **extra ) -> dict:
    return { "notes": "review_attach_pdf", "id": NOTE, "upload_id": UPLOAD, "title": "Latest Printable Deed PDF",
             "upload_sha256": SHA, "upload_bytes": len( PDF ), "upload_name": "Personal_Representative_Deed_REVISED.pdf",
             **extra }


def test_attach_fetches_the_upload_and_hands_the_adw_a_private_file_it_then_deletes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review, uploads = FakeReview(), FakeUploads()
        out = ask( attach_payload(), "implement", review, uploads, Path( tmp ) / "attach" )
        assert out.kind is OutcomeKind.SUCCEEDED, out.summary
        assert uploads.tasks[ 0 ].lease_token == LEASE
        op, flags, stoppable = review.calls[ 0 ]
        assert op == "notes.attach_pdf" and not stoppable
        assert flags[ "title" ] == "Latest Printable Deed PDF" and flags[ "upload-sha256" ] == SHA
        assert flags[ "name" ] == "Personal_Representative_Deed_REVISED.pdf"
        path, held, mode = review.seen[ 0 ]
        assert held == PDF and mode == 0o600 and not path.exists()
        assert out.details[ "attachment_id" ] == ATTACH and out.details[ "note_unchanged" ] is True


def test_attach_refuses_an_upload_that_is_not_the_one_described_or_cannot_be_fetched() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for uploads in ( FakeUploads( data=PDF + b" " ), FakeUploads( upload_id="u_" + "1" * 24 ), FakeUploads( fail=True )):
            review = FakeReview()
            out = ask( attach_payload(), "implement", review, uploads, Path( tmp ))
            assert out.kind is not OutcomeKind.SUCCEEDED and "nothing was attached" in out.summary
            assert review.calls == [] and list( Path( tmp ).iterdir()) == []


def test_attach_needs_the_gateway_description_and_is_absent_without_an_upload_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bare = { k: v for k, v in attach_payload().items() if not k.startswith( "upload_" ) or k == "upload_id" }
        out = ask( bare, "implement", FakeReview(), FakeUploads(), Path( tmp ))
        assert out.kind is not OutcomeKind.SUCCEEDED and "describes the upload" in out.summary
        out = ask( attach_payload(), "implement", FakeReview(), None, Path( tmp ))
        assert out.kind is not OutcomeKind.SUCCEEDED


def test_list_inspect_and_detach_are_plain_adw_runs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = FakeReview()
        assert ask({ "notes": "review_attachments", "id": NOTE }, "inspect", review, None, Path( tmp )).kind is OutcomeKind.SUCCEEDED
        ask({ "notes": "review_attachment", "id": NOTE, "attachment_id": ATTACH }, "inspect", review, None, Path( tmp ))
        ask({ "notes": "review_detach_pdf", "id": NOTE, "attachment_id": ATTACH }, "implement", review, None, Path( tmp ))
        assert [( c[ 0 ], c[ 1 ].get( "attachment-id" ), c[ 2 ]) for c in review.calls ] == [
            ( "notes.attachments", None, True ), ( "notes.attachment", ATTACH, True ), ( "notes.detach_pdf", ATTACH, False )]
        bad = ask({ "notes": "review_detach_pdf", "id": NOTE, "attachment_id": "../x" }, "implement", review, None, Path( tmp ))
        assert bad.kind is not OutcomeKind.SUCCEEDED and len( review.calls ) == 3


def test_the_process_adapter_passes_the_pdf_file_and_attachment_flags() -> None:
    uv = UvNotesReviewCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "/" ))
    argv = uv.argv( "notes.attach_pdf", { "note": NOTE, "pdf-file": "/tmp/a.pdf", "title": "T", "name": "d.pdf",
                                          "upload-sha256": SHA }, [], "/tmp/r.json" )
    assert "--pdf-file=/tmp/a.pdf" in argv and "--title=T" in argv and argv[ 4 ] == "notes.attach_pdf"
    assert "--attachment-id=" + ATTACH in uv.argv( "notes.detach_pdf", { "note": NOTE, "attachment-id": ATTACH }, [], "/r" )


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )

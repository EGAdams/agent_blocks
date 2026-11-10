#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Notes Review -- one traceable operation on a note's review highlights per run.

    cd /home/adamsl/agent_blocks
    uv run adws/adw_notes_review.py notes.inspect --note 2026-11-03_draft-personal-representative-s-deed-of-distribution.md
    uv run adws/adw_notes_review.py notes.get_active_highlight --note N [--page]
    uv run adws/adw_notes_review.py notes.stop_blink --note N
    uv run adws/adw_notes_review.py notes.activate --note N --item-id r-514098ba03
    uv run adws/adw_notes_review.py notes.activate_next --note N
    uv run adws/adw_notes_review.py notes.edit --note N --item-id ID --replace "new words" --expected-revision REV
    uv run adws/adw_notes_review.py notes.mark_resolved --note N --item-id ID --evidence "..." --expected-revision REV [--advance]
    uv run adws/adw_notes_review.py notes.verify --note N [--expect-item-id ID]
    uv run adws/adw_notes_review.py notes.rollback --note N --snapshot-id s-... [--chain]
    uv run adws/adw_notes_review.py notes.snapshots --note N
    uv run adws/adw_notes_review.py notes.watch --note N --until clear|change|active_change [--timeout 600]
    uv run adws/adw_notes_review.py notes.attach_pdf --note N --pdf-file deed.pdf --title "Latest Printable Deed PDF"
    uv run adws/adw_notes_review.py notes.attachments --note N
    uv run adws/adw_notes_review.py notes.attachment --note N --attachment-id a-0123456789ab
    uv run adws/adw_notes_review.py notes.detach_pdf --note N --attachment-id a-0123456789ab

What blinks is decided by the saved note (its `<!-- notes-review-focus: ... -->` marker, read by the same
rules as the dashboard's notes.js) and confirmed on the reloaded page through the browser.notes_state ADW.
Every write goes through the dashboard's token-protected Notes interface over the revision just read, passes
five guards (notes_review/guards.py), keeps an undo snapshot, and is read back. A PDF attached to a note is kept beside it and never
written into it (notes_review/attachments.py). Read
notes_review/DESIGN.md before changing any of it.

Phases: code(<operation>)
"""
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from browser_workflow_adw import Flag, run_browser_operation
from notes_review.catalog import PUBLIC, review_operations

FLAGS = [
    Flag( "note", "the note's file name", required=True ),
    Flag( "item_id", "a review item's id from notes.inspect (activate, edit, mark_resolved; watch, activate_next optional)" ),
    Flag( "replace", "the item's new words (edit; mark_resolved's resolved wording)" ),
    Flag( "evidence", "what record shows the item is resolved, in words (mark_resolved)" ),
    Flag( "approval", "what EG said approving it, in words (mark_resolved)" ),
    Flag( "expected_revision", "the revision you read (required for edit, mark_resolved)" ),
    Flag( "advance", "after resolving, make the next item blink in the same save (mark_resolved)", kind="bool" ),
    Flag( "page", "also reload the dashboard page and check it agrees (writes, get_active_highlight, watch)", kind="bool" ),
    Flag( "no_page", "verify the saved note only (verify)", kind="bool" ),
    Flag( "expect_item_id", "the item that should be blinking (verify)" ),
    Flag( "snapshot_id", "an undo point from a write's answer or notes.snapshots (rollback)" ),
    Flag( "chain", "also undo our own later writes (rollback)", kind="bool" ),
    Flag( "until", "clear | change | active_change (watch)" ),
    Flag( "timeout", "seconds to watch, 1-3600, default 60 (watch)", kind="float" ),
    Flag( "interval", "seconds between reads, default 3 (watch)", kind="float" ),
    Flag( "limit", "how many undo points to list (snapshots)", kind="int" ),
    Flag( "pdf_file", "the PDF on this machine to attach (attach_pdf)" ),
    Flag( "title", "the attachment's title, e.g. Latest Printable Deed PDF (attach_pdf)" ),
    Flag( "name", "the file name to show and download it as; default the file's own (attach_pdf)" ),
    Flag( "upload_sha256", "the sha256 the PDF was sent with; refused if the file hashes otherwise (attach_pdf)" ),
    Flag( "attachment_id", "an attachment's id, a-<12 hex>, from notes.attachments (attachment, detach_pdf)" ),
]


def main( argv: list[ str ], fixed: str = "" ) -> int:
    if fixed:
        argv = [ fixed ] + argv
    if not argv or argv[ 0 ] not in PUBLIC:
        sys.exit( f"usage: adw_notes_review.py <{ '|'.join( PUBLIC )}> --note N [flags]" )
    return run_browser_operation( PUBLIC[ argv[ 0 ]], __doc__, argv[ 1: ], FLAGS,
                                  catalog=lambda kit, console: review_operations(),
                                  default_transport="cdp", stamp_adw_id=True )


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))

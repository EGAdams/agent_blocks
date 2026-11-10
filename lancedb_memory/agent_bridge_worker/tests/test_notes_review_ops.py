"""The notes review_* operations offline: request shape, lanes, the flags the ADW gets, and honest outcomes.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_notes_review_ops
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.notes_review_process import UvNotesReviewCommands
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.notes_agent import NotesAgent
from agent_bridge_worker.notes_ops.commands import default_registry
from agent_bridge_worker.notes_ops.review import TABLE, ReviewCommandFailed
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_bridge_worker.tests.test_notes_ops import Dashboard, NoStore

NOTE = "2026-11-03_draft-personal-representative-s-deed-of-distribution.md"
ITEM = "r-514098ba03"
REV  = "a" * 64


class FakeReview:
    """Records each call and answers like the ADW would."""

    def __init__( self, answer: dict | None = None, fail: str = "" ) -> None:
        self.calls: list[ tuple ] = []
        self.answer = answer or { "ok": True, "line": "Line 11 is blinking: \"[VERIFY ...]\".",
                                  "data": { "note": NOTE, "revision": REV, "active_item_id": ITEM, "highlight_status": "blinking",
                                            "pending": [], "pending_count": 4, "adw_id": "abcd1234", "secret_extra": "x" }}
        self.fail = fail

    def run( self, operation, flags, switches, timeout, beat, stoppable, task_id="" ):
        self.calls.append(( operation, dict( flags ), list( switches ), timeout, stoppable, beat()))
        self.task_id = task_id
        if self.fail:
            raise ReviewCommandFailed( self.fail )
        return self.answer


def ask( payload: dict, operation: str, review=None, wired: bool = True ):
    t = ClaimedTask( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent="notes",
                     workspace="notes", operation=operation, approval_mode="read_only" if operation == "inspect" else "workspace_write",
                     prompt=json.dumps( payload ))
    dash = Dashboard()
    registry = default_registry( review=review or FakeReview()) if wired else default_registry()
    return NotesAgent( NoStore(), dash, dash, commands=registry ).run( t, Path( "/nonexistent" ), lambda: "continue" )


def test_inspect_runs_the_adw_and_carries_its_answer() -> None:
    review = FakeReview()
    out = ask({ "notes": "review_inspect", "id": NOTE }, "inspect", review )
    assert out.kind is OutcomeKind.SUCCEEDED and "blinking" in out.summary
    assert review.calls[ 0 ][ :3 ] == ( "notes.inspect", { "note": NOTE }, []) and review.calls[ 0 ][ 4 ] is True
    assert out.details[ "active_item_id" ] == ITEM and out.details[ "adw_id" ] == "abcd1234"
    assert "secret_extra" not in out.details                  # only the listed fields come back
    assert json.loads( out.body )[ "said" ].startswith( "Line 11" )


def test_edit_passes_values_and_switches_and_is_not_stoppable() -> None:
    review = FakeReview()
    out = ask({ "notes": "review_edit", "id": NOTE, "item_id": ITEM, "replace": "--new words", "expected_revision": REV,
                "page": True }, "implement", review )
    assert out.kind is OutcomeKind.SUCCEEDED
    op, flags, switches, _, stoppable, _ = review.calls[ 0 ]
    assert op == "notes.edit" and flags == { "note": NOTE, "item-id": ITEM, "replace": "--new words", "expected-revision": REV }
    assert switches == [ "page" ] and stoppable is False


def test_each_operation_runs_in_its_own_lane() -> None:
    for name, ( _, lane, required, _ ) in TABLE.items():
        if name == "review_attach_pdf":                   # needs an upload source: test_notes_attach_ops covers it
            continue
        payload = { "notes": name, "id": NOTE, **{ f: { "item_id": ITEM, "replace": "[VERIFY x]", "expected_revision": REV,
                                                        "snapshot_id": "s-0123456789",
                                                        "attachment_id": "a-0123456789ab" }[ f ] for f in required }}
        if name == "review_watch":
            payload[ "until" ] = "clear"
        if name == "review_mark_resolved":
            payload[ "evidence" ] = "the recorded plat shows page 31"
        wrong = "implement" if lane == "inspect" else "inspect"
        assert ask( payload, wrong ).kind is OutcomeKind.REFUSED, name
        assert ask( payload, lane ).kind is OutcomeKind.SUCCEEDED, name


def test_writes_need_what_the_guards_need_before_the_adw_runs() -> None:
    review = FakeReview()
    assert ask({ "notes": "review_edit", "id": NOTE, "item_id": ITEM, "replace": "x" }, "implement", review ).kind is OutcomeKind.REFUSED
    out = ask({ "notes": "review_mark_resolved", "id": NOTE, "item_id": ITEM, "expected_revision": REV }, "implement", review )
    assert out.kind is OutcomeKind.REFUSED and "evidence" in out.summary
    assert ask({ "notes": "review_watch", "id": NOTE }, "inspect", review ).kind is OutcomeKind.REFUSED
    assert ask({ "notes": "review_inspect" }, "inspect", review ).kind is OutcomeKind.REFUSED          # no note named
    assert ask({ "notes": "review_inspect", "id": NOTE, "replace": "x" }, "inspect", review ).kind is OutcomeKind.REFUSED
    assert ask({ "notes": "review_activate", "id": NOTE, "item_id": "not-an-id" }, "implement", review ).kind is OutcomeKind.REFUSED
    assert review.calls == []


def test_a_refusal_from_the_adw_is_a_failure_with_its_words_and_cause() -> None:
    review = FakeReview({ "ok": False, "line": "Nothing was written: that would resolve an open item; use mark_resolved.",
                          "data": { "cause": "guard", "note": NOTE, "adw_id": "beefcafe" }})
    out = ask({ "notes": "review_edit", "id": NOTE, "item_id": ITEM, "replace": "Liber 4", "expected_revision": REV },
              "implement", review )
    assert out.kind is OutcomeKind.FAILED and not out.retryable and "mark_resolved" in out.summary
    assert out.details[ "cause" ] == "guard" and json.loads( out.body )[ "cause" ] == "guard"


def test_an_adw_that_gives_no_answer_says_so() -> None:
    out = ask({ "notes": "review_inspect", "id": NOTE }, "inspect", FakeReview( fail = "exit status 1, no answer" ))
    assert out.kind is OutcomeKind.FAILED and "gave no answer" in out.summary


def test_a_worker_without_the_review_adw_refuses_by_name() -> None:
    out = ask({ "notes": "review_inspect", "id": NOTE }, "inspect", wired=False )
    assert out.kind is OutcomeKind.REFUSED and "not offered" in out.summary


def test_watch_gets_its_own_time_on_top() -> None:
    review = FakeReview()
    ask({ "notes": "review_watch", "id": NOTE, "until": "change", "timeout": 30 }, "inspect", review )
    assert review.calls[ 0 ][ 3 ] > 30 and review.calls[ 0 ][ 1 ][ "until" ] == "change"


def test_argv_is_fixed_table_and_flag_equals_value() -> None:
    uv = UvNotesReviewCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "/" ))
    argv = uv.argv( "notes.edit", { "note": NOTE, "replace": "--chain" }, [ "page" ], "/tmp/r.json" )
    assert argv[ :5 ] == [ "/uv", "run", "--quiet", "/adw.py", "notes.edit" ]
    assert "--replace=--chain" in argv and "--page" in argv and "--chain" not in argv and argv[ -1 ] == "--json-out=/tmp/r.json"
    for bad in (( "notes.purge", {}, []), ( "notes.edit", { "json-out": "x" }, []), ( "notes.edit", {}, [ "confirm" ])):
        try:
            uv.argv( bad[ 0 ], bad[ 1 ], bad[ 2 ], "/tmp/r.json" )
        except ReviewCommandFailed:
            continue
        raise AssertionError( bad )




def test_the_bridge_task_reaches_the_adw_as_an_environment_id_only() -> None:
    review = FakeReview()
    ask({ "notes": "review_inspect", "id": NOTE }, "inspect", review )
    assert review.task_id == TASK_ID                          # the dashboard logs the write under this task
    env = UvNotesReviewCommands.environment( TASK_ID )
    assert env[ "NOTES_BRIDGE_TASK" ] == TASK_ID and env[ "PYTHONUNBUFFERED" ] == "1"
    for bad in ( "", "notes-review", "t_XYZ", "t_ab; rm -rf /" ):
        assert "NOTES_BRIDGE_TASK" not in UvNotesReviewCommands.environment( bad ), bad


def test_rollback_passes_chain_and_page_through() -> None:
    review = FakeReview()
    out = ask({ "notes": "review_rollback", "id": NOTE, "snapshot_id": "s-0123456789", "expected_revision": REV,
                "chain": True, "page": True }, "implement", review )
    assert out.kind is OutcomeKind.SUCCEEDED
    op, flags, switches, _, _, _ = review.calls[ 0 ]
    assert op == "notes.rollback" and sorted( switches ) == [ "chain", "page" ]


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )

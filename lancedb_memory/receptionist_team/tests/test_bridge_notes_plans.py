"""Toyota's Notes Plans: every operation's form -> the exact worker prompt, and which ones wait for a yes.
Offline, no model.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_notes_plans
"""
import json

from ..adapters.bridge_specialist_runner import BridgeSpecialistRunner
from ..bridge_client import desk
from ..bridge_client.notes_plans import NOTES_PLANS
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm
from .test_bridge_specialists import NOTES

REV = "c" * 64
DEED = "2026-11-05_04-deed.md"
TRASHED = "20261107120000_2026-11-05_old.md"
B = SPECIALISTS["bridge_notes"].builder

# operation -> (form kwargs, the exact prompt, needs a yes)
CASES = {
    "list":       (dict(), {"notes": "list"}, False),
    "search":     (dict(query="Prince"), {"notes": "search", "query": "Prince"}, False),
    "read":       (dict(note_id=DEED), {"notes": "read", "id": DEED}, False),
    "highlights": (dict(note_id=DEED), {"notes": "highlights", "id": DEED}, False),
    "trash_list": (dict(), {"notes": "trash_list"}, False),
    "refresh":    (dict(note_id=DEED, known_revision=REV), {"notes": "refresh", "id": DEED, "known_revision": REV}, False),
    "update":     (dict(note_id=DEED, expected_revision=REV, edits=[{"line": 3, "replace": "x"}, {"section": "A", "replace": "y"},
                                                                    {"find": "a", "replace": "b"}]),
                   {"notes": "update", "id": DEED, "expected_revision": REV,
                    "edits": [{"line": 3, "replace": "x"}, {"section": "A", "replace": "y"}, {"find": "a", "replace": "b"}]}, True),
    "highlight":  (dict(note_id=DEED, expected_revision=REV, marks=[{"action": "add", "text": "Prince", "color": "yellow"},
                                                                    {"action": "clear", "all": True}]),
                   {"notes": "highlight", "id": DEED, "expected_revision": REV,
                    "marks": [{"action": "add", "text": "Prince", "color": "yellow"}, {"action": "clear", "all": True}]}, True),
    "create":     (dict(title="New Deed", text="hello", category=4),
                   {"notes": "create", "name": "New Deed", "text": "hello", "category": 4}, True),
    "rename":     (dict(note_id=DEED, expected_revision=REV, new_name="Final", category=0),
                   {"notes": "rename", "id": DEED, "expected_revision": REV, "new_name": "Final", "category": 0}, True),
    "trash":      (dict(note_id=DEED, expected_revision=REV), {"notes": "trash", "id": DEED, "expected_revision": REV}, True),
    "restore":    (dict(trash_id=TRASHED), {"notes": "restore", "trash_id": TRASHED}, True),
    "purge":      (dict(trash_id=TRASHED),
                   {"notes": "purge", "trash_id": TRASHED, "authorization": f"EG authorizes permanently deleting {TRASHED}"}, True),
}


async def test_every_operation_builds_the_exact_prompt_and_the_right_lane():
    assert set(CASES) | {"status", "marker_write", "marker_clear"} == set(NOTES_PLANS)
    for op, (kwargs, prompt, needs_yes) in CASES.items():
        req, said = B.build(SpecialistForm(operation=op, **kwargs), NOTES)
        assert json.loads(req.prompt) == prompt, op
        assert req.needs_confirmation is needs_yes, op
        assert (req.operation == "implement") is needs_yes, op
        if needs_yes:
            assert said, op


async def test_purge_says_it_is_permanent_and_that_yes_is_the_authorization():
    _, said = B.build(SpecialistForm(operation="purge", trash_id=TRASHED), NOTES)
    assert "permanently" in said and "cannot be undone" in said and "authorization" in said


async def test_what_cannot_be_safe_is_refused_before_anything_is_sent():
    bad = (dict(operation="search"),                                                          # nothing to search for
           dict(operation="refresh", note_id=DEED),                                            # never read: nothing to compare to
           dict(operation="highlight", note_id=DEED, marks=[{"action": "add", "text": "x", "color": "yellow"}]),   # no revision
           dict(operation="highlight", note_id=DEED, expected_revision=REV, marks=[{"action": "add", "text": "x"}]),
           dict(operation="highlight", note_id=DEED, expected_revision=REV, marks=[{"action": "add", "text": "x", "color": "<script>"}]),
           dict(operation="update", note_id=DEED, expected_revision=REV, edits=[{"find": "a", "line": 2, "replace": "b"}]),
           dict(operation="update", note_id=DEED, expected_revision=REV, edits=[{"replace": "b"}]),
           dict(operation="create", title="x"), dict(operation="create", text="x"),
           dict(operation="rename", note_id=DEED, expected_revision=REV),
           dict(operation="trash", note_id=DEED),
           dict(operation="restore", trash_id="../../etc/passwd"),
           dict(operation="purge", trash_id=DEED),                                             # a live note is not a trash id
           dict(operation="purge"),
           dict(operation="vacuum"))
    for kwargs in bad:
        try:
            B.build(SpecialistForm(**kwargs), NOTES)
            raise AssertionError(kwargs)
        except FormProblem:
            pass


async def test_the_runner_supplies_revisions_for_every_revision_checked_operation():
    runner = BridgeSpecialistRunner("bridge_notes", filler=object(), pending=object(), notices=object())
    desk.NOTE_REVISIONS.remember(DEED, REV)
    for op in ("update", "highlight", "rename", "trash"):
        form = SpecialistForm(operation=op, note_id=DEED, expected_revision="f" * 64)   # the model's value is discarded
        runner._attach_revision(form)
        assert form.expected_revision == REV, op
    form = SpecialistForm(operation="refresh", note_id=DEED, known_revision="f" * 64)
    runner._attach_revision(form)
    assert form.known_revision == REV and form.expected_revision is None
    form = SpecialistForm(operation="trash", note_id="never_read.md")
    runner._attach_revision(form)
    assert form.expected_revision is None


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

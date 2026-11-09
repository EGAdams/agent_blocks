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
    assert set(CASES) | set(REVIEW_CASES) | {"status", "marker_write", "marker_clear"} == set(NOTES_PLANS)
    for op, (kwargs, prompt, needs_yes) in CASES.items():
        req, said = B.build(SpecialistForm(operation=op, **kwargs), NOTES)
        assert json.loads(req.prompt) == prompt, op
        assert req.needs_confirmation is needs_yes, op
        assert (req.operation == "implement") is needs_yes, op
        if needs_yes:
            assert said, op


ITEM = "r-514098ba03"
SNAP = "s-0123456789"
REVIEW_CASES = {
    "review_inspect":              (dict(note_id=DEED), {"notes": "review_inspect", "id": DEED}, False),
    "review_get_active_highlight": (dict(note_id=DEED), {"notes": "review_get_active_highlight", "id": DEED}, False),
    "review_verify":               (dict(note_id=DEED), {"notes": "review_verify", "id": DEED}, False),
    "review_snapshots":            (dict(note_id=DEED, limit=5), {"notes": "review_snapshots", "id": DEED, "limit": 5}, False),
    "review_watch":                (dict(note_id=DEED, until="clear", limit=30),
                                    {"notes": "review_watch", "id": DEED, "until": "clear", "timeout": 30}, False),
    "review_stop_blink":           (dict(note_id=DEED), {"notes": "review_stop_blink", "id": DEED}, True),
    "review_activate":             (dict(note_id=DEED, item_id=ITEM, expected_revision=REV),
                                    {"notes": "review_activate", "id": DEED, "item_id": ITEM, "expected_revision": REV}, True),
    "review_activate_next":        (dict(note_id=DEED), {"notes": "review_activate_next", "id": DEED}, True),
    "review_edit":                 (dict(note_id=DEED, item_id=ITEM, replace="[VERIFY Liber 4]", expected_revision=REV),
                                    {"notes": "review_edit", "id": DEED, "item_id": ITEM, "replace": "[VERIFY Liber 4]",
                                     "expected_revision": REV}, True),
    "review_mark_resolved":        (dict(note_id=DEED, item_id=ITEM, expected_revision=REV, evidence="plat image page 31",
                                         replace="Liber 4 of Plats, Page 31.", advance=True),
                                    {"notes": "review_mark_resolved", "id": DEED, "item_id": ITEM, "expected_revision": REV,
                                     "evidence": "plat image page 31", "replace": "Liber 4 of Plats, Page 31.",
                                     "advance": True}, True),
    "review_rollback":             (dict(note_id=DEED, snapshot_id=SNAP, expected_revision=REV, advance=True),
                                    {"notes": "review_rollback", "id": DEED, "snapshot_id": SNAP, "expected_revision": REV,
                                     "chain": True}, True),
}


async def test_every_review_operation_builds_the_worker_prompt_in_its_lane():
    for op, (kwargs, prompt, needs_yes) in REVIEW_CASES.items():
        req, said = B.build(SpecialistForm(operation=op, **kwargs), NOTES)
        assert json.loads(req.prompt) == prompt, op
        assert req.needs_confirmation is needs_yes and (req.operation == "implement") is needs_yes, op
        if needs_yes:
            assert said, op


async def test_resolving_says_on_what_grounds_and_an_edit_says_it_stays_open():
    _, said = B.build(SpecialistForm(**{**REVIEW_CASES["review_mark_resolved"][0], "operation": "review_mark_resolved"}), NOTES)
    assert "plat image page 31" in said and "yes confirms" in said
    _, said = B.build(SpecialistForm(operation="review_mark_resolved", note_id=DEED, item_id=ITEM, expected_revision=REV,
                                     approval="EG: yes, that one is settled"), NOTES)
    assert "because you said" in said
    _, said = B.build(SpecialistForm(**{**REVIEW_CASES["review_edit"][0], "operation": "review_edit"}), NOTES)
    assert "stays open" in said


async def test_a_review_change_that_cannot_be_safe_is_refused_before_anything_is_sent():
    bad = (dict(operation="review_inspect"),                                                   # no note
           dict(operation="review_edit", note_id=DEED, item_id=ITEM, replace="x"),              # never read
           dict(operation="review_edit", note_id=DEED, item_id=ITEM, expected_revision=REV),    # no new words
           dict(operation="review_activate", note_id=DEED, item_id="line 9"),
           dict(operation="review_mark_resolved", note_id=DEED, item_id=ITEM, expected_revision=REV),   # no grounds
           dict(operation="review_rollback", note_id=DEED, expected_revision=REV),
           dict(operation="review_rollback", note_id=DEED, snapshot_id="../x", expected_revision=REV),
           dict(operation="review_watch", note_id=DEED),
           dict(operation="review_watch", note_id=DEED, until="forever"))
    for kwargs in bad:
        try:
            B.build(SpecialistForm(**kwargs), NOTES)
            raise AssertionError(kwargs)
        except FormProblem:
            pass


async def test_a_review_answer_is_remembered_so_the_next_change_carries_its_revision():
    from types import SimpleNamespace
    runner = BridgeSpecialistRunner("bridge_notes", filler=object(), pending=object(), notices=object())
    fresh = "e" * 64
    run = SimpleNamespace(request=SimpleNamespace(agent="notes", prompt=json.dumps({"notes": "review_inspect", "id": DEED})),
                          result=SimpleNamespace(succeeded=True, body=json.dumps({"said": "x", "id": DEED, "revision": fresh,
                                                                                  "active_item_id": ITEM})))
    runner._remember_revision(run)
    for op in ("review_edit", "review_mark_resolved", "review_rollback", "review_stop_blink"):
        form = SpecialistForm(operation=op, note_id=DEED, expected_revision="f" * 64)
        runner._attach_revision(form)
        assert form.expected_revision == fresh, op
    form = SpecialistForm(operation="review_edit", note_id=DEED, item_id="active", replace="[VERIFY x]")
    runner._attach_revision(form)
    assert form.item_id == ITEM                                   # "the blinking one" is the id last reported
    form = SpecialistForm(operation="review_edit", note_id="never_read.md", item_id="active")
    runner._attach_revision(form)
    assert form.item_id is None                                   # nothing reported: the plan asks which item


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


async def test_a_result_with_a_said_sentence_is_read_aloud_and_other_bodies_are_unchanged():
    from ..bridge_client.models import BridgeResult
    from ..bridge_client.workflow import RespondPhase

    def spoken(body):
        return RespondPhase.result_sentence(BridgeResult(task_id="t_" + "a" * 24, state="succeeded", ready=True,
                                                         ok=True, summary="s", body=body))
    assert spoken(json.dumps({"said": "Created x.md.", "revision": "r" * 64, "diff": "huge"})).endswith("): Created x.md.")
    assert "revision" not in spoken(json.dumps({"said": "Created x.md.", "revision": "r"}))
    assert spoken("plain words").endswith("): plain words")
    assert spoken('{"no_said": 1}').endswith('): {"no_said": 1}')
    assert spoken('{"said": 5}').endswith('{"said": 5}')


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

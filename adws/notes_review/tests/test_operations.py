"""The notes.* operations end to end over fakes: the whole review workflow, every guard, undo."""
from notes_review.catalog import COMMANDS, PUBLIC
from notes_review.document import ReviewDocument, revision_of

from .fakes import DEED, DEED_NAME, PLAT, FakePage, plane


def run(p, public, **args):
    return COMMANDS[public](p).run({"note": DEED_NAME, "adw_id": "abcd1234", **args})


def test_inspect_reports_the_blinking_item_and_every_field(tmp_path):
    p, _ = plane(tmp_path)
    out = run(p, "notes.inspect")
    assert out.ok
    for key in ("note", "revision", "active_item_id", "active_text", "highlight_status", "pending"):
        assert key in out.data
    assert out.data["active_text"] == PLAT and out.data["highlight_status"] == "blinking"
    assert out.data["revision"] == revision_of(DEED)


def test_get_active_highlight_checks_the_page(tmp_path):
    p, _ = plane(tmp_path)
    out = run(p, "notes.get_active_highlight", page=True)
    assert out.ok and PLAT in out.line and out.data["verification"]["page_agrees"]


def test_page_hiding_the_text_fails_the_check(tmp_path):
    p, _ = plane(tmp_path, page=lambda s: FakePage(s, hide_text=True))
    out = run(p, "notes.get_active_highlight", page=True)
    assert not out.ok and "hides" in out.line


def test_the_whole_workflow_then_a_byte_for_byte_undo(tmp_path):
    p, store = plane(tmp_path)
    first = run(p, "notes.inspect")
    plat_id = first.data["active_item_id"]

    stop = run(p, "notes.stop_blink", page=True)
    assert stop.ok and stop.data["highlight_status"] == "stopped"
    assert PLAT in store.notes[DEED_NAME]                       # no word deleted
    first_snapshot = stop.data["snapshot_id"]

    again = run(p, "notes.activate", item_id=plat_id, page=True)
    assert again.ok and again.data["active_item_id"] == plat_id

    new_words = '[VERIFY PLAT RECORD REFERENCE: Confirm "Liber 9 of Plats, Page 99" against the plat image.]'
    edited = run(p, "notes.edit", item_id=plat_id, replace=new_words, expected_revision=again.data["revision"])
    assert edited.ok and edited.data["active_text"] == new_words
    assert edited.data["new_item_id"] != plat_id

    nxt = run(p, "notes.activate_next", page=True)
    assert nxt.ok and nxt.data["active_line"] == 11

    check = run(p, "notes.verify", expect_item_id=nxt.data["active_item_id"])
    assert check.ok and check.data["verification"]["page_revision_matches"]

    undo = run(p, "notes.rollback", snapshot_id=first_snapshot, chain=True)
    assert undo.ok and undo.data["byte_for_byte"]
    assert store.notes[DEED_NAME] == DEED


def test_edit_needs_the_revision_that_was_read(tmp_path):
    p, store = plane(tmp_path)
    item = run(p, "notes.inspect").data["active_item_id"]
    out = run(p, "notes.edit", item_id=item, replace="[VERIFY other words]")
    assert not out.ok and out.data["cause"] == "guard" and store.writes == 0
    stale = run(p, "notes.edit", item_id=item, replace="[VERIFY other words]", expected_revision="0" * 64)
    assert not stale.ok and "changed since" in stale.line and store.writes == 0


def test_an_edit_may_not_resolve_an_open_item(tmp_path):
    p, store = plane(tmp_path)
    first = run(p, "notes.inspect")
    out = run(p, "notes.edit", item_id=first.data["active_item_id"], replace="Liber 9 of Plats, Page 99.",
              expected_revision=first.data["revision"])
    assert not out.ok and "mark_resolved" in out.line and store.writes == 0


def test_mark_resolved_needs_evidence_or_approval(tmp_path):
    p, store = plane(tmp_path)
    first = run(p, "notes.inspect")
    out = run(p, "notes.mark_resolved", item_id=first.data["active_item_id"], replace="Liber 9 of Plats, Page 99.",
              expected_revision=first.data["revision"])
    assert not out.ok and "evidence" in out.line and store.writes == 0


def test_mark_resolved_refuses_a_line_that_still_reads_open(tmp_path):
    p, store = plane(tmp_path)
    first = run(p, "notes.inspect")
    out = run(p, "notes.mark_resolved", item_id=first.data["active_item_id"],
              evidence="the plat image shows page 99", expected_revision=first.data["revision"])
    assert not out.ok and "still read as an open item" in out.line and store.writes == 0


def test_mark_resolved_with_evidence_and_advance(tmp_path):
    p, store = plane(tmp_path)
    first = run(p, "notes.inspect")
    out = run(p, "notes.mark_resolved", item_id=first.data["active_item_id"], replace="Liber 9 of Plats, Page 99.",
              evidence="the recorded plat image shows Liber 9 page 99", advance=True,
              expected_revision=first.data["revision"], page=True)
    assert out.ok, out.line
    assert out.data["active_line"] == 11 and out.data["pending_count"] == first.data["pending_count"] - 1
    snap = p.snapshots.get(out.data["snapshot_id"])
    assert snap.reason.startswith("the recorded plat") and snap.adw_id == "abcd1234"


def test_a_plain_open_line_needs_resolved_wording(tmp_path):
    p, _ = plane(tmp_path)
    doc = ReviewDocument(DEED_NAME, DEED)
    title = next(i for i in doc.items if i.line == 13)
    out = run(p, "notes.mark_resolved", item_id=title.id, evidence="title search shows none", expected_revision=doc.revision)
    assert not out.ok and out.data["cause"] == "needs_replacement"


def test_rollback_never_overwrites_someone_elses_newer_edit(tmp_path):
    p, store = plane(tmp_path)
    stop = run(p, "notes.stop_blink")
    store.notes[DEED_NAME] += "EG typed this.\n"
    out = run(p, "notes.rollback", snapshot_id=stop.data["snapshot_id"])
    assert not out.ok and out.data["cause"] == "newer_edits"
    assert store.notes[DEED_NAME].endswith("EG typed this.\n")


def test_activating_a_list_item_is_refused_with_the_reason(tmp_path):
    p, store = plane(tmp_path)
    doc = ReviewDocument(DEED_NAME, DEED)
    listed = next(i for i in doc.items if i.line == 20)
    out = run(p, "notes.activate", item_id=listed.id)
    assert not out.ok and "list item" in out.line and store.writes == 0


def test_activate_next_from_a_stopped_note_starts_at_the_top(tmp_path):
    p, _ = plane(tmp_path)
    run(p, "notes.stop_blink")
    out = run(p, "notes.activate_next")
    assert out.ok and out.data["active_line"] == 9


def test_watch_until_clear_sees_the_highlight_go(tmp_path):
    p, store = plane(tmp_path)
    original_sleep = p.clock.sleep

    def resolve_while_waiting(seconds):
        original_sleep(seconds)
        store.notes[DEED_NAME] = store.notes[DEED_NAME].replace("{{highlight:yellow}}" + PLAT + "{{/highlight}}",
                                                                "Liber 9 of Plats, Page 99.")
    p.clock.sleep = resolve_while_waiting
    out = run(p, "notes.watch", until="clear", timeout=30, interval=1)
    assert out.ok and out.data["met"] and out.data["polls"] == 2


def test_watch_times_out_honestly(tmp_path):
    p, _ = plane(tmp_path)
    out = run(p, "notes.watch", until="change", timeout=5, interval=1)
    assert not out.ok and out.data["cause"] == "timeout" and "did not change" in out.line


def test_unknown_note_and_bad_name(tmp_path):
    p, _ = plane(tmp_path)
    assert run(p, "notes.inspect", note="nope.md").data["cause"] == "note_not_found"
    assert run(p, "notes.inspect", note="../etc/passwd").data["cause"] == "no_note"


def test_registry_names_every_operation_once():
    assert len(set(PUBLIC.values())) == len(PUBLIC) == 15
    assert all(public.replace(".", "_") == internal for public, internal in PUBLIC.items()
               if public != "notes.get_active_highlight") and PUBLIC["notes.get_active_highlight"] == "notes_get_active_highlight"

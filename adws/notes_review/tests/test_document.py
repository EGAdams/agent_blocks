"""ReviewDocument against the dashboard's rules (notes.js), on a synthetic deed shaped like the live one."""
import os
from pathlib import Path

import pytest

from notes_review.document import DocumentRefusal, ReviewDocument, changed_lines, item_id, review_kind

from .fakes import DEED, DEED_NAME, PLAT


def doc(text=DEED, name=DEED_NAME):
    return ReviewDocument(name, text)


def test_items_are_the_lines_the_page_paints():
    d = doc()
    assert d.deed
    lines = [i.line for i in d.items]
    assert 3 not in lines                      # the "items in [brackets]" banner
    assert 22 not in lines                     # a signing blank, [________]
    assert lines == [9, 11, 13, 20, 24]


def test_the_focused_yellow_span_blinks_and_its_id_is_stable():
    d = doc()
    active = d.active()
    assert active.line == 9 and active.yellow and active.target == PLAT
    assert active.id == item_id(PLAT)
    edited = d.text.replace("Lot 1,", "Lot 2,")
    assert ReviewDocument(DEED_NAME, edited).active().id == active.id


def test_a_list_item_cannot_blink_and_says_why():
    item = next(i for i in doc().items if i.line == 20)
    assert not item.activatable and "list item" in item.why_not


def test_empty_marker_stops_all_blinking_and_changes_only_the_marker_line():
    d = doc()
    stopped = d.with_focus("")
    assert changed_lines(d.text, stopped) == [15]
    assert ReviewDocument(DEED_NAME, stopped).active() is None
    assert ReviewDocument(DEED_NAME, stopped).items[0].yellow   # the highlight itself is kept


def test_focus_on_a_plain_paragraph_line_blinks_it():
    d = doc()
    title = next(i for i in d.items if i.line == 13)
    moved = ReviewDocument(DEED_NAME, d.with_focus(title.target))
    assert moved.active().line == 13


def test_focus_matching_two_lines_blinks_nothing():
    text = DEED + "\n{{highlight:yellow}}" + PLAT + "{{/highlight}}\n"
    d = doc(text)
    assert not d.focus_unique and d.active() is None


def test_a_note_without_a_marker_gets_one_appended():
    text = DEED.replace("<!-- notes-review-focus: " + PLAT + " -->\n", "")
    d = doc(text)
    assert d.active() is None                       # no legacy attorney line here
    out = d.with_focus(PLAT)
    assert out.endswith(f"<!-- notes-review-focus: {PLAT} -->\n")
    assert ReviewDocument(DEED_NAME, out).active().line == 9


def test_legacy_rule_blinks_the_attorney_line_in_the_authority_section():
    text = "# Personal Representative's Deed\n\n## Authority of the Personal Representative\n\n[VERIFY WITH ATTORNEY: letters.]\n"
    d = ReviewDocument("x-deed.md", text)
    assert d.active().line == 5


def test_editing_a_yellow_item_keeps_the_highlight_and_moves_the_focus():
    d = doc()
    item = d.active()
    new = "[VERIFY PLAT RECORD REFERENCE: Confirm Liber 9, Page 99 against the image.]"
    out = d.with_item_text(item, new)
    assert set(changed_lines(d.text, out)) == {9, 15}
    after = ReviewDocument(DEED_NAME, out)
    assert after.active().target == new and after.active().yellow


def test_without_highlight_keeps_the_words_and_clears_the_focus():
    d = doc()
    out = d.without_highlight(d.active())
    assert PLAT in out.splitlines()[8] and "{{highlight" not in out.splitlines()[8]
    assert ReviewDocument(DEED_NAME, out).focus == ""


def test_marker_text_that_would_break_the_comment_is_refused():
    with pytest.raises(DocumentRefusal):
        doc().with_focus("a --> b")


def test_crlf_line_endings_survive_a_change():
    text = DEED.replace("\n", "\r\n")
    d = doc(text)
    out = d.with_focus("")
    assert out.count("\r\n") == text.count("\r\n") and "\n" not in out.replace("\r\n", "")


def test_review_kind_matches_the_dashboard():
    assert review_kind("Prepared by: [NAME]") == "preparer"
    assert review_kind("[VERIFY legal description]") == "legal-description"
    assert review_kind("Items in [brackets] are open") == ""
    assert review_kind("Signed: [____]") == ""
    assert review_kind("plain words") == ""


@pytest.mark.skipif(not os.environ.get("NOTES_REVIEW_DEED"), reason="NOTES_REVIEW_DEED names a real deed copy")
def test_real_deed_copy():
    path = Path(os.environ["NOTES_REVIEW_DEED"])
    d = ReviewDocument(path.name, path.read_text(encoding="utf-8"))
    assert d.active() is not None and d.active().yellow

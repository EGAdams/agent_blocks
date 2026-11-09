"""Highlights: the blinking-line classifier, browser.read's highlight report and browser.notes_state. No browser.

    cd adws && uv run --with pydantic --with pytest pytest -q browser_control/tests/test_highlights.py
"""
from __future__ import annotations

from browser_control.highlights import blinking, classify, classify_page, is_clear, is_highlight, normalize, parse_color
from browser_control.notes_page import BACK, CHOICE, EDITOR_TITLE
from browser_control.operations import NOTES_TAB_CSS

from .test_operations import FakeElement, make, run

DEED = "2026-11-03_draft-personal-representative-s-deed-of-distribution.md"
DASH = {"background_color": "rgba(0, 0, 0, 0)", "color": "rgb(17, 17, 17)", "opacity": "1", "visibility": "visible"}
BLINK = {"name": "notes-active-deed-warning", "play_state": "running", "duration_ms": 1400, "iterations": "infinite",
         "keyframes": [{"offset": 0, "background": "rgb(255, 245, 157)"}, {"offset": 0.49, "background": "rgb(255, 245, 157)"},
                       {"offset": 0.5, "background": "rgba(0, 0, 0, 0)"}, {"offset": 1, "background": "rgba(0, 0, 0, 0)"}]}


def row(text, classes=(), style=None, animations=(), samples=()):
    return {"text": text, "tag": "mark", "classes": list(classes), "css": "#p > mark", "style": {**DASH, **(style or {})},
            "animations": list(animations), "samples": list(samples)}


# ---- the classifier -----------------------------------------------------------------------------------------

def test_colours_are_read_in_every_spelling_the_page_uses():
    assert parse_color("rgb(255, 245, 157)") == (255, 245, 157, 1.0)
    assert parse_color("rgba(0, 0, 0, 0)")[3] == 0 and is_clear("transparent") and is_clear("rgb(0 0 0 / 0%)")
    assert is_highlight("#fff59d") and is_highlight("rgb(255, 235, 59)") and is_highlight("yellow")
    assert not is_highlight("rgb(255, 255, 255)") and not is_highlight("rgb(17, 17, 17)") and not is_highlight(None)


def test_the_dashboards_own_animation_is_a_blink_with_the_text_visible():
    state = classify(row("[VERIFY PLAT RECORD REFERENCE: x]", ["notes-deed-review-active-line"], animations=[BLINK]))
    assert state.blinking and state.highlighted and state.text_visible
    assert state.animation == "notes-active-deed-warning" and "none and yellow" in state.evidence[0]


def test_samples_alone_prove_a_blink_and_a_steady_mark_is_highlighted_only():
    on, off = "rgb(255, 245, 157)|rgb(17, 17, 17)|1|visible", "rgba(0, 0, 0, 0)|rgb(17, 17, 17)|1|visible"
    sampled = classify(row("x", samples=[on] * 7 + [off] * 8))
    assert sampled.blinking and sampled.colors_seen == ["none", "yellow"] and "changed 1 times" in sampled.evidence[0]
    steady = classify(row("y", style={"background_color": "rgb(255, 245, 157)"}, samples=[on.replace("rgba", "rgb")] * 3))
    assert steady.highlighted and not steady.blinking and steady.evidence == ["steady yellow background"]


def test_a_blink_that_hides_the_text_is_reported_as_text_not_visible():
    fade = {**BLINK, "keyframes": BLINK["keyframes"] + [{"offset": 0.5, "opacity": "0"}]}
    assert classify(row("z", animations=[fade])).text_visible is False
    sampled = classify(row("z", samples=["rgb(255, 245, 157)|rgb(17,17,17)|1|visible", "rgba(0,0,0,0)|rgb(17,17,17)|0|visible"]))
    assert sampled.blinking and not sampled.text_visible


def test_a_paused_or_finite_or_colourless_animation_is_not_a_blink():
    for anim in ({**BLINK, "play_state": "paused"}, {**BLINK, "iterations": 1},
                 {**BLINK, "keyframes": [{"offset": 0, "opacity": "1"}, {"offset": 1, "opacity": "0.5"}]}):
        assert not classify(row("q", animations=[anim])).blinking


def test_a_page_keeps_only_highlighted_items_and_names_the_blinking_one():
    states = classify_page({"items": [row("plain"), row("lit", animations=[BLINK]),
                                      row("steady", style={"background_color": "#fff59d"})]})
    assert [s.text for s in states] == ["lit", "steady"] and [s.text for s in blinking(states)] == ["lit"]


def test_normalize_matches_rendered_text_to_source_text():
    assert normalize("{{highlight:yellow}}[VERIFY *PLAT*  `x`]{{/highlight}}") == normalize("[VERIFY PLAT x]")


# ---- browser.read -------------------------------------------------------------------------------------------

def test_read_reports_which_text_is_highlighted_and_blinking(tmp_path):
    ops, world = make(tmp_path, running=["chrome"])
    found = {"root_found": True, "items": [row("[VERIFY X]", animations=[BLINK]), row("plain")]}
    original = world.attach
    def attach(kind, tab_id):
        page = original(kind, tab_id)
        page.highlights = lambda root, limit, ms: found
        return page
    world.attach = attach
    out = run(ops, "browser.read")
    assert out.ok and out.data["blinking"] == ["[VERIFY X]"] and len(out.data["highlights"]) == 1
    assert "blinking: '[VERIFY X]'" in out.line and out.data["highlights"][0]["text_visible"]


def test_read_on_a_page_without_the_probe_still_reads_text(tmp_path):
    ops, _ = make(tmp_path, running=["chrome"])
    out = run(ops, "browser.read")
    assert out.ok and out.data["highlights"] == [] and out.data["blinking"] == []


# ---- browser.notes_state ------------------------------------------------------------------------------------

class Dashboard:
    """The Notes page: a NOTES button, the landing list (a deed inside a closed category), the editor."""

    def __init__(self, open_note=None, listed=(DEED,), hidden_words=False):
        self.open_note, self.revealed, self.reloads = open_note, [], 0
        self.notes = FakeElement(text="NOTES", tag="button", selectors={NOTES_TAB_CSS}, attrs={"class": "tab"})
        self.back = FakeElement(text="Back to Notes", tag="button", selectors={BACK})
        self.choices = []
        for name in listed:
            choice = FakeElement(text="" if hidden_words else name, tag="button", selectors={CHOICE},
                                 attrs={"aria-label": f"{name} — unread, new or updated"} if hidden_words else None)
            choice.text_content = name
            choice.reveal = (lambda n=name: self.revealed.append(n))
            self.choices.append(choice)

    def elements(self):
        for choice in self.choices:
            if choice.clicked:
                self.open_note, choice.clicked = choice.text_content, False
        if self.back.clicked:
            self.open_note, self.back.clicked = None, False
        title = FakeElement(text=self.open_note or "", tag="h3", selectors={EDITOR_TITLE}, shown=bool(self.open_note))
        landing = [] if self.open_note else self.choices
        return [self.notes, title, self.back] + landing


def notes_world(tmp_path, dash, found=None):
    ops, world = make(tmp_path, running=["chrome"])
    original = world.attach
    def attach(kind, tab_id):
        world.elements = dash.elements()
        page = original(kind, tab_id)
        page.text_digest = lambda css: {"found": True, "chars": 3000 if dash.open_note else 0, "sha256": "bec6" + "0" * 60}
        page.highlights = lambda root, limit, ms: found or {"root_found": True, "items": []}
        def reload(ready="complete"):
            dash.reloads, dash.reload_ready = dash.reloads + 1, ready
        page.reload = reload
        return page
    world.attach = attach
    return ops, world


def test_notes_state_opens_the_note_through_the_list_and_names_the_blinking_line(tmp_path):
    dash = Dashboard()
    found = {"root_found": True, "items": [row("[VERIFY PLAT RECORD REFERENCE: x]", animations=[BLINK]),
                                           row("[VERIFY TITLE EXCEPTIONS: y]", style={"background_color": "#fff59d"})]}
    ops, _ = notes_world(tmp_path, dash, found)
    out = run(ops, "browser.notes_state", note=DEED)
    assert out.ok and out.data["verified"], out.line
    assert dash.revealed == [DEED] and dash.open_note == DEED, "a note in a closed category is revealed, then opened"
    assert out.data["active"] == "[VERIFY PLAT RECORD REFERENCE: x]" and out.data["revision"].startswith("bec6")
    assert "Its text stays visible" in out.line and "2 highlighted items" in out.line
    assert [s.name for s in ops["browser_notes_state"].steps] == ["browser_launch", "browser_visibility",
                                                                 "browser_dashboard_tab", "browser_notes_state"]


def test_notes_state_goes_back_from_another_note_and_reloads_when_asked(tmp_path):
    dash = Dashboard(open_note="other.md", listed=("other.md", DEED))
    ops, _ = notes_world(tmp_path, dash)
    out = run(ops, "browser.notes_state", note=DEED, reload=True)
    assert out.ok and dash.open_note == DEED and dash.reloads == 1 and out.data["reloaded"]
    assert dash.reload_ready == "interactive", "the embedded pages are not waited for; the Notes tab is"
    assert out.data["active"] is None and "Nothing in it is blinking" in out.line


def test_notes_state_refuses_a_missing_or_unlisted_note(tmp_path):
    ops, _ = notes_world(tmp_path, Dashboard())
    assert run(ops, "browser.notes_state").data["cause"] == "no_note"
    assert run(ops, "browser.notes_state", note="../etc/passwd").data["cause"] == "no_note"
    out = run(ops, "browser.notes_state", note="2099-01-01_absent.md")
    assert not out.ok and out.data["cause"] == "note_not_listed"


def test_notes_state_finds_a_note_whose_button_words_the_page_hides(tmp_path):
    """The live dashboard (2026-11-09) hides its note buttons' words with CSS: innerText is "", the file name is only
    in textContent, and an unread note's aria-label adds "— unread, new or updated". It is still found by name."""
    dash = Dashboard(listed=("other.md", DEED), hidden_words=True)
    ops, _ = notes_world(tmp_path, dash)
    out = run(ops, "browser.notes_state", note=DEED)
    assert out.ok and dash.open_note == DEED, out.line

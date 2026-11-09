"""browser.notes_state: what the dashboard's Notes page shows for one note -- which line blinks, read off the page.

The last phase of the `browser.notes_state` sequence (Launch -> Visibility -> DashboardTab -> NotesState). It
attaches to the dashboard tab DashboardTab found, optionally reloads it (the "did it survive a refresh?" check),
presses NOTES if another tab is showing, opens the named note through the page's own note list (a note inside a
collapsed category is revealed first, never typed into a box), and waits until the editor shows that note.

Then it reads two things, both inside the page:

- the editor text's sha256 and length (`text_digest`) -- the same sha256 `notes_store` calls a revision, so the
  caller can tell "the page shows the saved version" from "the page shows something older"; the text itself
  never leaves the page;
- every highlighted element of the rendered preview (`highlights`): classes, computed style, running animations
  and sampled background, classified by `highlights.classify_page` into blinking / steady / text visible.

Nothing is edited here: no key is typed and the note is never saved. The page is read, and the answer says what
it read, including `hidden_page` (a hidden tab does not run the page's 3 s refresh, so it may lag the file).
"""
from __future__ import annotations

import re
from typing import Any

from browser_workflows.operations.common import OperationOutcome, ok, refused

from .highlights import blinking, classify_page
from .operations import NOTES_TAB_CSS, BrowserOperation, safe_url

NOTE_NAME = re.compile(r"^[A-Za-z0-9][\w.\-() ]{0,160}\.md$")
EDITOR_TITLE = "#notes-editor-title"
EDITOR_TEXT = "#notes-editor-text"
PREVIEW = "#notes-editor-preview"
BACK = "#notes-editor-back"
CHOICE = "#notes-landing button.notes-choice"
SAMPLE_MS = 1500


def _choice_name(button) -> str:
    """A note button's file name: its visible text, or -- when the page's CSS hides those words and draws them
    another way, which leaves innerText empty -- the node's own text. Never the aria-label, which adds
    "unread, new or updated" to the name."""
    shown = (button.text or "").strip()
    if shown:
        return shown
    own = getattr(button, "text_content", None)
    return (own or "").strip() if isinstance(own, str) else ""


class NotesState(BrowserOperation):
    name = "browser_notes_state"
    description = ("browser.notes_state: open one note on the dashboard's Notes page and read which of its lines "
                   "is highlighted and which one blinks, plus the revision the page shows (nothing is edited)")
    wait_seconds = 15.0

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        self._up(kind)
        note = str(args.get("note") or args.get("text") or "").strip()
        if not NOTE_NAME.fullmatch(note):
            return refused("Name the note to open by its file name, e.g. --note=2026-11-03_draft.md.",
                           browser=kind.name, verified=False, cause="no_note")
        tab = args.get("tab") or None
        if args.get("reload"):
            page = self._plane.attacher.attach(kind, tab)
            try:
                page.reload()
            finally:
                page.release()
            if not self._until(kind, tab, lambda p: bool(p.find(NOTES_TAB_CSS))):
                return self._stuck(kind, "the dashboard did not come back after the reload", "reload_failed")
        problem = self._open_note(kind, tab, note)
        if problem:
            return problem
        page = self._plane.attacher.attach(kind, tab)
        try:
            digest = page.text_digest(EDITOR_TEXT)
            found = page.highlights(PREVIEW, 60, int(args.get("sample_ms") or SAMPLE_MS))
            url = safe_url(page.current_url)
        finally:
            page.release()
        states = classify_page(found)
        lit = blinking(states)
        rows = [s.model_dump() for s in states]
        if lit:
            said = (f"{note} is open on the dashboard. Blinking now: " + "; ".join(repr(s.text[:160]) for s in lit)
                    + (". Its text stays visible." if all(s.text_visible for s in lit) else
                       ". WARNING: its text is hidden for part of the blink."))
        else:
            said = f"{note} is open on the dashboard. Nothing in it is blinking."
        said += f" {len(states)} highlighted item{'s' if len(states) != 1 else ''} in all."
        return ok(said, browser=kind.name, tab=tab, url=url, note=note, revision=digest.get("sha256"),
                  chars=digest.get("chars"), highlights=rows, blinking=[s.text for s in lit],
                  active=lit[0].text if lit else None, hidden_page=bool(found.get("hidden_page")),
                  reloaded=bool(args.get("reload")), verified=bool(found.get("root_found")))

    # -- getting the note on screen ------------------------------------------------------------

    def _open_note(self, kind, tab, note: str) -> OperationOutcome | None:
        if not self._showing(kind, tab, note):
            self._press_if_present(kind, tab, NOTES_TAB_CSS, only_if_inactive=True)
            self._press_if_present(kind, tab, BACK, only_if_displayed=True)
            if not self._until(kind, tab, lambda p: bool(p.find(CHOICE))):
                return self._stuck(kind, "the Notes page never listed its notes", "notes_not_listed")
            page = self._plane.attacher.attach(kind, tab)
            try:
                choice = next((b for b in page.find(CHOICE) if _choice_name(b) == note), None)
                if choice is None:
                    return refused(f"The Notes page lists no note called {note}.", browser=kind.name,
                                   verified=False, cause="note_not_listed")
                reveal = getattr(choice, "reveal", None)
                if reveal:
                    reveal()
                choice.click()
            finally:
                page.release()
        if not self._until(kind, tab, lambda p: self._title(p) == note and (p.text_digest(EDITOR_TEXT).get("chars") or 0) > 0):
            return self._stuck(kind, f"{note} did not open in the editor", "note_not_open")
        return None

    def _showing(self, kind, tab, note: str) -> bool:
        page = self._plane.attacher.attach(kind, tab)
        try:
            title = page.find(EDITOR_TITLE)
            return bool(title) and title[0].is_displayed() and (title[0].text or "").strip() == note
        finally:
            page.release()

    @staticmethod
    def _title(page) -> str:
        found = page.find(EDITOR_TITLE)
        return (found[0].text or "").strip() if found and found[0].is_displayed() else ""

    def _press_if_present(self, kind, tab, css: str, only_if_inactive: bool = False,
                          only_if_displayed: bool = False) -> None:
        page = self._plane.attacher.attach(kind, tab)
        try:
            found = page.find(css)
            if not found:
                return
            element = found[0]
            if only_if_inactive and "active" in (element.get_attribute("class") or "").split():
                return
            if only_if_displayed and not element.is_displayed():
                return
            element.click()
        finally:
            page.release()

    def _until(self, kind, tab, ready) -> bool:
        deadline = self._plane.clock.monotonic() + self.wait_seconds
        while True:
            page = self._plane.attacher.attach(kind, tab)
            try:
                if ready(page):
                    return True
            finally:
                page.release()
            if self._plane.clock.monotonic() >= deadline:
                return False
            self._plane.clock.sleep(0.5)

    def _stuck(self, kind, what: str, cause: str) -> OperationOutcome:
        return refused(f"On the dashboard's Notes page, {what} within {int(self.wait_seconds)}s.", browser=kind.name,
                       verified=False, cause=cause)

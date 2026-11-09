"""Fakes for the notes_review interfaces, and a synthetic deed shaped like the live one (no real names)."""
from __future__ import annotations

from notes_review.document import revision_of
from notes_review.guards import default_chain
from notes_review.interfaces import NoteMoved
from notes_review.models import PageReading
from notes_review.observers import NoObserver
from notes_review.plane import ReviewPlane
from notes_review.snapshots import FileSnapshotStore

DEED_NAME = "2026-01-01_draft-personal-representative-s-deed-of-distribution.md"
PLAT = '[VERIFY PLAT RECORD REFERENCE: Confirm "Liber 9 of Plats, Page 99" against the recorded plat.]'
DEED = (
    "# Personal Representative's Deed of Distribution\n"
    "\n"
    "Items in [brackets] are open.\n"
    "\n"
    "## Property\n"
    "\n"
    "Lot 1, Example Addition, according to the recorded plat.\n"
    "\n"
    "{{highlight:yellow}}" + PLAT + "{{/highlight}}\n"
    "\n"
    "*Plat research: the registry lists the plat; VERIFY the page number against the image.*\n"
    "\n"
    "[VERIFY TITLE EXCEPTIONS: Check the records for easements.]\n"
    "\n"
    "<!-- notes-review-focus: " + PLAT + " -->\n"
    "Subject to the exceptions above.\n"
    "\n"
    "## Authority and Distribution\n"
    "\n"
    "- [VERIFY a list item cannot blink]\n"
    "\n"
    "Signed: [________]\n"
    "\n"
    "[TO COMPLETE AT SIGNING: Fill in the date.]\n"
)


class MemoryStore:
    def __init__(self, notes: dict[str, str]) -> None:
        self.notes = dict(notes)
        self.writes = 0

    def read(self, note: str) -> tuple[str, str]:
        if note not in self.notes:
            raise FileNotFoundError(note)
        return self.notes[note], revision_of(self.notes[note])

    def write(self, note: str, text: str, expected_revision: str) -> str:
        if revision_of(self.notes[note]) != expected_revision:
            raise NoteMoved("it is newer")
        self.notes[note] = text
        self.writes += 1
        return revision_of(text)


class FakePage:
    """Shows whatever the store holds, blinking what the saved text says (like the real page)."""

    def __init__(self, store: MemoryStore, hide_text: bool = False) -> None:
        self.store = store
        self.hide_text = hide_text
        self.reads = 0

    def read(self, note: str, reload: bool) -> PageReading:
        from notes_review.document import ReviewDocument
        self.reads += 1
        text, revision = self.store.read(note)
        active = ReviewDocument(note, text).active()
        return PageReading(ok=True, adw_id="feedbeef", revision=revision,
                           blinking=[active.target.strip("[]")] if active else [],
                           text_visible=not self.hide_text, reloaded=reload)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.n = 0

    def now_iso(self) -> str:
        self.n += 1
        return f"2026-11-09T00:00:{self.n:02d}.000000+00:00"

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def plane(tmp_path, notes: dict[str, str] | None = None, page=None):
    store = MemoryStore(notes if notes is not None else {DEED_NAME: DEED})
    return ReviewPlane(store=store, snapshots=FileSnapshotStore(tmp_path / "snaps"),
                       page=page(store) if page else FakePage(store), guards=default_chain(),
                       observer=NoObserver(), clock=FakeClock()), store

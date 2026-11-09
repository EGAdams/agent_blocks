"""Pydantic models that cross a boundary in notes_review: review items, snapshots (Mementos), page readings."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReviewItem(BaseModel):
    """One line of a note the page paints yellow, and whether it is the one blinking."""
    model_config = ConfigDict(extra="forbid")

    id: str
    line: int
    kind: str
    text: str
    target: str
    yellow: bool = False
    open: bool = True
    active: bool = False
    activatable: bool = True
    why_not: str = ""
    section: str = ""


class NoteSnapshot(BaseModel):
    """Memento: the whole note exactly as it was before one write, and the revision that write produced.

    Rollback writes `before` back only while the note still holds `after_revision` -- someone else's newer edit
    is never overwritten by an undo."""
    model_config = ConfigDict(extra="forbid")

    id: str
    note: str
    operation: str
    adw_id: str = ""
    taken_at: str
    before: str
    before_revision: str
    after_revision: str = ""
    item_id: str = ""
    reason: str = ""


class PageReading(BaseModel):
    """What the dashboard page showed for a note, read by the browser.notes_state ADW."""
    model_config = ConfigDict(extra="forbid")

    ok: bool
    line: str = ""
    adw_id: str = ""
    revision: str | None = None
    blinking: list[str] = Field(default_factory=list)
    text_visible: bool = True
    highlighted: int = 0
    reloaded: bool = False
    cause: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class Verification(BaseModel):
    """Saved text and page agree with what the operation meant to leave behind."""
    model_config = ConfigDict(extra="forbid")

    saved: bool
    saved_revision: str
    expected_active: str | None = None
    saved_active: str | None = None
    page_checked: bool = False
    page_revision_matches: bool | None = None
    page_blinking: list[str] = Field(default_factory=list)
    page_agrees: bool | None = None
    page_text_visible: bool | None = None
    page_adw_id: str = ""
    problems: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.saved and not self.problems


class ProposedChange(BaseModel):
    """What every guard is shown before a write: the note before and after, the item, the caller's grounds."""
    model_config = ConfigDict(extra="forbid")

    operation: str
    note: str
    before: str
    after: str
    item: ReviewItem | None = None
    new_item: ReviewItem | None = None
    evidence: str = ""
    approval: str = ""
    expected_revision: str = ""
    current_revision: str = ""
    allowed_lines: list[int] = Field(default_factory=list)

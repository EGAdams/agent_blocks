"""The interfaces notes_review programs against. Only `plane.default_plane()` names a concrete class."""
from __future__ import annotations

from typing import Protocol

from .models import NoteSnapshot, PageReading, ProposedChange


class INoteStore(Protocol):
    """The saved note: read it with its revision; write it only over the revision the caller read."""

    def read(self, note: str) -> tuple[str, str]: ...

    def write(self, note: str, text: str, expected_revision: str) -> str:
        """The new revision. Raises `NoteMoved` when the note no longer holds `expected_revision`."""
        ...


class ISnapshotStore(Protocol):
    """Mementos of notes before each write, so any write can be undone."""

    def save(self, snapshot: NoteSnapshot) -> None: ...

    def get(self, snapshot_id: str) -> NoteSnapshot | None: ...

    def for_note(self, note: str) -> list[NoteSnapshot]:
        """Newest first."""
        ...


class IPageReader(Protocol):
    """What the dashboard page shows for a note: the revision it holds and which highlight blinks."""

    def read(self, note: str, reload: bool) -> PageReading: ...


class IReviewObserver(Protocol):
    def notify(self, event: str, data: dict) -> None: ...


class IClock(Protocol):
    def now_iso(self) -> str: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class IReviewGuard(Protocol):
    """One link of the Chain of Responsibility every write passes. None = no objection."""

    def objection(self, change: ProposedChange) -> str | None: ...


class NoteMoved(RuntimeError):
    """The note changed after it was read: someone else's edit is newer, so nothing was written."""

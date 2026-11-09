"""Observers of notes_review events: an activity log (JSON lines), a fan-out Composite and a Null Object."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .interfaces import IReviewObserver


class JsonlActivity:
    """One line per event -- what changed and why, never the note's text."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def notify(self, event: str, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **data}
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
        os.chmod(self._path, 0o600)


class FanOut:
    def __init__(self, observers: list[IReviewObserver]) -> None:
        self._observers = list(observers)

    def notify(self, event: str, data: dict) -> None:
        for observer in self._observers:
            observer.notify(event, data)


class NoObserver:
    def notify(self, event: str, data: dict) -> None:
        return None

"""ILedger: one JSON line per run, append-only -- who asked for what, on which project, and what came of it."""
from __future__ import annotations

import fcntl
from pathlib import Path

from ..models import LedgerEntry


class JsonlLedger:
    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, entry: LedgerEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(entry.model_dump_json() + "\n")

    def recent(self, project: str, limit: int) -> list[LedgerEntry]:
        if not self._path.is_file():
            return []
        rows = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                entry = LedgerEntry.model_validate_json(line)
            except ValueError:
                continue
            if not project or entry.project == project:
                rows.append(entry)
        return rows[-max(1, limit):][::-1]

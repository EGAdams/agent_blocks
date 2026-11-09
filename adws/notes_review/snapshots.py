"""FileSnapshotStore: each Memento as one JSON file, kept beside the ADW data, never in the repository."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .models import NoteSnapshot

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class FileSnapshotStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def save(self, snapshot: NoteSnapshot) -> None:
        folder = self._root / _SAFE.sub("_", snapshot.note)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{snapshot.taken_at.replace(':', '')}_{snapshot.id}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(snapshot.model_dump_json(indent=1), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(path)

    def get(self, snapshot_id: str) -> NoteSnapshot | None:
        if not re.fullmatch(r"s-[0-9a-f]{10}", snapshot_id or ""):
            return None
        for path in self._root.glob(f"*/*_{snapshot_id}.json"):
            return NoteSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    def for_note(self, note: str) -> list[NoteSnapshot]:
        folder = self._root / _SAFE.sub("_", note)
        found = [NoteSnapshot.model_validate_json(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
        return sorted(found, key=lambda s: s.taken_at, reverse=True)

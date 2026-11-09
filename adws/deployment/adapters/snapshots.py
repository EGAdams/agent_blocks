"""ISnapshotter: the content hash of every file a project's release covers (its scope, its suffixes,
less its exclusions). Symlinks are never followed."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..models import SKIP_DIRS, Project


class FileSnapshotter:
    def scan(self, project: Project, root: Path) -> dict[str, str]:
        found: dict[str, str] = {}
        for scope in project.scope or [""]:
            base = root / scope
            if not base.is_dir():
                continue
            for folder, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in files:
                    path = Path(folder) / name
                    rel = path.relative_to(root).as_posix()
                    if path.is_symlink() or not project.covers(rel):
                        continue
                    found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return dict(sorted(found.items()))

"""IReleaseStore on disk: each release is one JSON file, and every file it names is kept once, by its
sha256, so a rollback restores exact bytes and storing the same file twice costs nothing."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..models import Release


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class FileReleaseStore:
    def __init__(self, home: Path) -> None:
        self._releases, self._blobs = home / "releases", home / "blobs"

    def _blob(self, sha: str) -> Path:
        return self._blobs / sha[:2] / sha

    def save(self, release: Release, root: Path) -> None:
        for rel, sha in release.files.items():
            blob = self._blob(sha)
            if blob.exists():
                continue
            data = (root / rel).read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError(f"{rel} changed while the release was being recorded; nothing was recorded")
            atomic_write(blob, data)
        atomic_write(self._releases / release.project / f"{release.release_id}.json",
                     release.model_dump_json(indent=1).encode())

    def recent(self, project: str, limit: int) -> list[Release]:
        folder = self._releases / project
        if not folder.is_dir():
            return []
        names = sorted(folder.glob("*.json"), reverse=True)[:max(1, limit)]
        return [Release.model_validate_json(p.read_text(encoding="utf-8")) for p in names]

    def latest(self, project: str) -> Release | None:
        for release in self.recent(project, 50):
            if release.kind != "pre_rollback":
                return release
        return None

    def get(self, project: str, release_id: str) -> Release | None:
        if not release_id.replace("-", "").replace("_", "").isalnum():
            return None
        path = self._releases / project / f"{release_id}.json"
        return Release.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    def content(self, sha256: str) -> bytes:
        data = self._blob(sha256).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("a stored file no longer matches its hash")
        return data

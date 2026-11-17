"""This machine's private, temporary copy of what was asked for -- never the library, never for long.

    <root>/originals/<id>/<name>     a photograph copied off the phone because it was asked for
    <root>/renditions/<id>/...       the JPEGs made from it
    <root>/last_list.json            the last listing, so "number three" means something

Everything older than `KEEP_SECONDS` is removed on the next run. The root is 0700 and outside the repository.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from .models import RemotePhoto

KEEP_SECONDS = 24 * 3600


class PhotoCache:
    def __init__(self, root: Path, now=time.time) -> None:
        self._root, self._now = root, now
        for name in ("originals", "renditions"):
            (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def photo_id(device: str, photo: RemotePhoto) -> str:
        return hashlib.sha256(f"{device}|{photo.key}|{photo.modified}".encode()).hexdigest()[:20]

    def original_dir(self, device: str, photo: RemotePhoto) -> Path:
        return self._root / "originals" / self.photo_id(device, photo)

    def original(self, device: str, photo: RemotePhoto) -> Path | None:
        """The copy already here, if it is. A photograph does not change, so it is not copied twice."""
        path = self.original_dir(device, photo) / photo.name
        return path if path.is_file() and path.stat().st_size > 0 else None

    def rendition_dir(self, device: str, photo: RemotePhoto) -> Path:
        return self._root / "renditions" / self.photo_id(device, photo)

    def purge(self) -> int:
        removed, cutoff = 0, self._now() - KEEP_SECONDS
        for kind in ("originals", "renditions"):
            for entry in (self._root / kind).iterdir():
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
        return removed


class FileLastListing:
    """ILastListing in one JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, device: str, photos: list[dict]) -> None:
        self._path.write_text(json.dumps({"device": device, "photos": photos}), encoding="utf-8")
        self._path.chmod(0o600)

    def load(self) -> tuple[str, list[dict]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return str(data["device"]), [p for p in data["photos"] if isinstance(p, dict)]
        except (OSError, ValueError, KeyError, TypeError):
            return "", []

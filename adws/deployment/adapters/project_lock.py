"""IProjectLock with flock: a second change to the same project is refused at once, not queued."""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path


class Busy(RuntimeError):
    pass


class FlockProjectLock:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    @contextlib.contextmanager
    def hold(self, project: str):
        self._folder.mkdir(parents=True, exist_ok=True)
        with open(self._folder / f"{project}.lock", "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy(f"another deployment of {project} is running") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

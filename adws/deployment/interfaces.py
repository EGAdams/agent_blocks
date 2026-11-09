"""The Deployment ADW's interfaces: one Protocol per responsibility. `kit.py` is the only module that names
the classes implementing them; operations see only these."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import HealthCheck, LedgerEntry, Project, Release, Step


class IProjectCatalog(Protocol):
    def names(self) -> list[str]: ...
    def get(self, name: str) -> Project: ...                       # KeyError for a project not configured


class ISourceTree(Protocol):
    """The git side of a project. Never commits, never pushes, never discards a local change."""
    def head(self, root: Path) -> str | None: ...
    def dirty(self, root: Path) -> list[str]: ...                   # paths with uncommitted changes
    def fetch(self, root: Path, remote: str, branch: str) -> str: ...   # the remote branch's commit
    def changed(self, root: Path, old: str, new: str) -> list[str]: ...
    def fast_forward(self, root: Path, commit: str) -> None: ...      # refuses anything but a fast-forward
    def reset_keep(self, root: Path, commit: str) -> None: ...        # moves HEAD back, keeping local edits


class ISnapshotter(Protocol):
    def scan(self, project: Project, root: Path) -> dict[str, str]: ...   # path -> sha256, the files in scope


class IReleaseStore(Protocol):
    def save(self, release: Release, root: Path) -> None: ...          # keeps every file's content by hash
    def latest(self, project: str) -> Release | None: ...
    def get(self, project: str, release_id: str) -> Release | None: ...
    def recent(self, project: str, limit: int) -> list[Release]: ...
    def content(self, sha256: str) -> bytes: ...


class IServiceManager(Protocol):
    def state(self, unit: str) -> str: ...                           # "active", "inactive", "failed", ...
    def restart(self, unit: str) -> None: ...
    def restart_later(self, unit: str, seconds: int, tag: str) -> None: ...   # detached; survives this process


class IHealthProbe(Protocol):
    def check(self, check: HealthCheck) -> tuple[bool, str]: ...


class ILogReader(Protocol):
    def tail(self, unit: str, lines: int) -> str: ...
    def tail_file(self, path: Path, lines: int) -> str: ...


class ICheckRunner(Protocol):
    def run(self, argv: list[str], root: Path, timeout: int) -> tuple[bool, str]: ...


class ILedger(Protocol):
    def record(self, entry: LedgerEntry) -> None: ...
    def recent(self, project: str, limit: int) -> list[LedgerEntry]: ...


class ITrace(Protocol):
    """Every step a run takes, in order, with what it found. Written beside the ledger, by ADW id."""
    def step(self, name: str, ok: bool = True, **detail) -> None: ...
    def steps(self) -> list[Step]: ...


class IProjectLock(Protocol):
    """One change at a time per project: a deploy and a rollback never interleave."""
    def hold(self, project: str): ...                                  # a context manager; raises if busy


class IClock(Protocol):
    def now(self) -> str: ...
    def sleep(self, seconds: float) -> None: ...
    def monotonic(self) -> float: ...

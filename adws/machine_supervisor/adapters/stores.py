"""Small file-backed pieces: watch state, incidents, the mail outbox, locks, the run log, the clock, the waker.

Everything lives under one folder outside the repository (`~/.local/share/machine_supervisor/`), so a `git pull`
or a rollback of the code never touches a machine's own record.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models import Incident, Mail, WatchState


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def detach(argv: list[str], log: Path, env: dict[str, str]) -> None:
    """Start a child in its own session and never wait for it. A program that cannot start is a log line."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as out:
        try:
            subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=env)
        except OSError as error:
            out.write(f"could not start {argv[0]}: {error}\n")


class FileWatchState:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> WatchState:
        try:
            return WatchState.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return WatchState()

    def save(self, state: WatchState) -> None:
        write_atomic(self._path, state.model_dump_json(indent=2))


class FileIncidentStore:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def save(self, incident: Incident) -> None:
        write_atomic(self._folder / f"{incident.id}.json", incident.model_dump_json(indent=2))

    def get(self, incident_id: str) -> Incident:
        try:
            return Incident.model_validate_json((self._folder / f"{incident_id}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(f"no incident {incident_id}") from None


class FileOutbox:
    """Mail that could not be sent. The next watch run tries again; nothing is dropped unsent."""
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def hold(self, mail: Mail) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        write_atomic(self._folder / f"{stamp}_{uuid.uuid4().hex[:8]}.json", mail.model_dump_json(indent=2))

    def pending(self) -> list[tuple[str, Mail]]:
        found = []
        for path in sorted(self._folder.glob("*.json")) if self._folder.exists() else []:
            with contextlib.suppress(ValueError, FileNotFoundError):
                found.append((path.name, Mail.model_validate_json(path.read_text(encoding="utf-8"))))
        return found

    def drop(self, key: str) -> None:
        (self._folder / key).unlink(missing_ok=True)


class FlockLock:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    @contextlib.contextmanager
    def hold(self, name: str):
        self._folder.mkdir(parents=True, exist_ok=True)
        with open(self._folder / f"{name}.lock", "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def busy(self, name: str) -> bool:
        with self.hold(name) as mine:
            return not mine


class FileRunLog:
    def __init__(self, path: Path, echo: bool = True) -> None:
        self._path, self._echo = path, echo

    def quiet(self) -> None:
        """Stop echoing: for a run whose stdout is read by a program."""
        self._echo = False

    def line(self, text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().strftime('%F %T')} {text}\n")
        if self._echo:
            print(text, flush=True)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class DetachedWaker:
    """IWaker: starts adw_machine_supervise.py in its own session and never reads its exit status, so the cron
    run that woke it ends at once. The supervisor's own lock stops a second one starting beside it."""
    def __init__(self, python: str, script: Path, log: Path, env: dict[str, str]) -> None:
        self._python, self._script, self._log, self._env = python, script, log, env

    def wake(self, incident_id: str) -> None:
        detach([self._python, str(self._script), "--incident", incident_id], self._log, self._env)


class DetachedNudge:
    """INudge: starts one fixed command the same way. The command takes its own lock, so a second nudge is harmless."""
    def __init__(self, argv: list[str], log: Path, env: dict[str, str]) -> None:
        self._argv, self._log, self._env = argv, log, env

    def nudge(self) -> None:
        detach(self._argv, self._log, self._env)

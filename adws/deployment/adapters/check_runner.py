"""ICheckRunner: one configured test command, in the project's root, with a deadline. Only argv lists
from projects.json reach this; a request never supplies a command."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .journal_logs import redact


class SubprocessChecks:
    def run(self, argv: list[str], root: Path, timeout: int) -> tuple[bool, str]:
        argv = [os.path.expanduser(a) for a in argv]
        try:
            done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, env={**os.environ, "CI": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s"
        except OSError as exc:
            return False, f"could not start: {type(exc).__name__}"
        tail = redact(((done.stdout or "") + (done.stderr or ""))[-3000:])
        return done.returncode == 0, tail

"""ILogReader over `journalctl --user`, with anything that looks like a credential masked before it leaves."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .systemd_services import SystemdUserServices

SECRET = re.compile(r"(?i)(bearer\s+|token[\"'=:\s]+|password[\"'=:\s]+|secret[\"'=:\s]+|api[_-]?key[\"'=:\s]+|"
                    r"authorization[\"'=:\s]+)[^\s\"',;]{6,}")
LONG = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|[A-Za-z0-9_-]{48,})\b")


def redact(text: str) -> str:
    text = SECRET.sub(lambda m: m.group(1) + "[redacted]", text)
    return LONG.sub("[redacted]", text)


class JournalLogs:
    def tail(self, unit: str, lines: int) -> str:
        unit = SystemdUserServices._unit(unit)
        done = subprocess.run(["journalctl", "--user", "-u", unit, "-n", str(max(1, min(400, lines))),
                               "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
        return redact((done.stdout or done.stderr)[-60_000:])

    def tail_file(self, path: Path, lines: int) -> str:
        """The last lines of a log file a unit appends to itself (StandardOutput=append:...)."""
        if not path.is_file():
            return f"{path} does not exist"
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 400_000))
            text = handle.read().decode("utf-8", "replace")
        return redact("\n".join(text.splitlines()[-max(1, min(400, lines)):])[-60_000:])

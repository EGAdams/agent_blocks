"""IWindowsLink: can this process reach Windows, and can WSL see what Windows opens?

Two host facts decided the first failures on the live box, and neither is a timeout problem:

* WSL interop is per session. A systemd service has no WSL_INTEROP, so it falls back to /run/WSL/1_interop,
  which can point at a session whose Windows side is gone (`UtilAcceptVsock: accept4 failed 110`). Then
  chrome.exe is never started at all. `ensure()` finds a socket that works and exports it for this process.
* Chrome binds its DevTools port to Windows' 127.0.0.1. WSL reaches that only with `networkingMode=mirrored`;
  in NAT mode it never answers. `explain_unreachable()` says so instead of "the port never answered".
"""
from __future__ import annotations

import glob
import os
import subprocess
from collections.abc import Callable

WINDOWS_CMD = "/mnt/c/Windows/System32/cmd.exe"
Probe = Callable[[dict[str, str]], bool]
Run = Callable[[list[str]], str]


def _can_run_windows(env: dict[str, str]) -> bool:
    try:
        return subprocess.run([WINDOWS_CMD, "/c", "exit"], env=env, cwd="/mnt/c", capture_output=True,
                              timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _output(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class WslInteropLink:
    def __init__(self, probe: Probe = _can_run_windows, sockets: Callable[[], list[str]] | None = None,
                 environ: dict[str, str] | None = None, run: Run = _output) -> None:
        self._probe, self._run = probe, run
        self._environ = os.environ if environ is None else environ
        self._sockets = sockets or (lambda: sorted(glob.glob("/run/WSL/*_interop"), key=_age, reverse=True))
        self._checked: tuple[bool, str] | None = None

    def ensure(self) -> tuple[bool, str]:
        """Make Windows programs startable from here. (True, "") or (False, why). Checked once per process."""
        if self._checked is None:
            self._checked = self._find()
        return self._checked

    def _find(self) -> tuple[bool, str]:
        if self._probe(dict(self._environ)):
            return True, ""
        for candidate in self._sockets():
            if self._probe({**self._environ, "WSL_INTEROP": candidate}):
                self._environ["WSL_INTEROP"] = candidate          # for this process and what it starts
                return True, ""
        return False, ("WSL cannot start Windows programs from here (every interop socket timed out, "
                       "'accept4 failed 110'), so Chrome was not launched. Restarting WSL from Windows clears it.")

    def explain_unreachable(self, port: int) -> str:
        mode = self._run(["wslinfo", "--networking-mode"]).lower()
        if mode and mode != "mirrored":
            return (f"WSL networking is '{mode}', so Windows' 127.0.0.1:{port} (where Chrome listens) is not reachable from "
                    "here. Set networkingMode=mirrored in the Windows user's .wslconfig and restart WSL.")
        return f"Chrome was started but nothing answered on 127.0.0.1:{port}."


def _age(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0

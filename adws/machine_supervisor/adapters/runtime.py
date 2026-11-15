"""The environment every child process gets, and one way to run a child.

cron, a systemd unit and a non-interactive SSH session each start with almost no environment. Four things are
missing and each one has cost a silent failure before: ~/.local/bin on PATH (claude, uv), XDG_RUNTIME_DIR
(without it `systemctl --user` cannot find the user's manager), a live WSL interop socket (without it no
Windows program starts, so no toast), and ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL must stay unset so the Claude
CLI uses this machine's subscription login.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from browser_control.link import WslInteropLink

from ..models import Ran

EXTRA_PATH = ("/usr/local/bin", "/snap/bin", "/usr/bin", "/bin")


_base: dict[str, str] | None = None
_interop: str | None = None


def _base_env() -> dict[str, str]:
    global _base
    if _base is None:
        env = dict(os.environ)
        home = env.setdefault("HOME", str(Path.home()))
        have = [part for part in env.get("PATH", "").split(":") if part]
        env["PATH"] = ":".join(dict.fromkeys([f"{home}/.local/bin", *have, *EXTRA_PATH]))
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            env.pop(name, None)
        _base = env
    return _base


def child_env(extra: dict[str, str] | None = None, windows: bool = False) -> dict[str, str]:
    """`windows=True` also proves the WSL interop socket (one probe per process); a probe run costs a
    Windows process start, so a check that only talks to Linux never pays for it."""
    global _interop
    env = dict(_base_env())
    if windows:
        if _interop is None:
            WslInteropLink(environ=env).ensure()      # sets WSL_INTEROP in env when the inherited one is dead
            _interop = env.get("WSL_INTEROP", "")
        elif _interop:
            env["WSL_INTEROP"] = _interop
    env.update(extra or {})
    return env


def run(argv: list[str], timeout: int, stdin: str | None = None, env: dict[str, str] | None = None,
        cwd: str | None = None) -> Ran:
    """Never raises: a missing program or a timeout is a failed run with the reason in `err`."""
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout,
                              env=env if env is not None else child_env(), cwd=cwd)
        return Ran(code=done.returncode, out=done.stdout, err=done.stderr)
    except subprocess.TimeoutExpired as late:
        out = late.stdout.decode(errors="replace") if isinstance(late.stdout, bytes) else (late.stdout or "")
        return Ran(code=124, out=out, err=f"timed out after {timeout}s")
    except OSError as error:
        return Ran(code=127, err=f"{type(error).__name__}: {error}")

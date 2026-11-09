"""IServiceManager over `systemctl --user`. A unit the worker itself runs in is restarted by a transient
timer (`systemd-run --on-active`), so the task can answer before its own process is replaced."""
from __future__ import annotations

import re
import subprocess

UNIT = re.compile(r"^[A-Za-z0-9@._-]{1,80}$")


class ServiceError(RuntimeError):
    pass


class SystemdUserServices:
    def __init__(self, timeout: int = 90) -> None:
        self._timeout = timeout

    def _call(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=self._timeout, stdin=subprocess.DEVNULL)

    @staticmethod
    def _unit(unit: str) -> str:
        if not UNIT.fullmatch(unit):
            raise ServiceError("not a unit name")
        return unit

    def state(self, unit: str) -> str:
        done = self._call(["systemctl", "--user", "is-active", self._unit(unit)])
        return (done.stdout.strip() or "unknown").splitlines()[0]

    def restart(self, unit: str) -> None:
        done = self._call(["systemctl", "--user", "restart", self._unit(unit)])
        if done.returncode != 0:
            raise ServiceError(f"restart {unit} failed: {(done.stderr or done.stdout).strip()[:300]}")

    def restart_later(self, unit: str, seconds: int, tag: str) -> None:
        name = f"adw-deploy-{tag}-{self._unit(unit).removesuffix('.service')}"
        done = self._call(["systemd-run", "--user", f"--on-active={max(1, seconds)}", f"--unit={name}", "--collect",
                           "systemctl", "--user", "restart", unit])
        if done.returncode != 0:
            raise ServiceError(f"could not schedule the restart of {unit}: {(done.stderr or done.stdout).strip()[:300]}")

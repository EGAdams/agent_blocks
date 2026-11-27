"""IProbe: one class per kind of check, and a registry that picks by `Check.kind`.

A new kind of check is one class and one `register()` call in kit.py, not an edit here. No probe raises.
The probes that watch the supervisor's own machinery (todos, held mail, ADW crashes, disk, heartbeats) are in
self_probes.py.
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request

from ..interfaces import IProbe
from ..models import Check, CheckResult
from .runtime import run


class HttpProbe:
    def look(self, check: Check) -> CheckResult:
        try:
            with urllib.request.urlopen(check.target, timeout=check.timeout) as reply:
                code = reply.status
        except urllib.error.HTTPError as error:
            code = error.code
        except Exception as error:                                   # refused, timed out, DNS: all "no answer"
            return CheckResult(check=check.name, ok=False, detail=f"no answer from {check.target} ({error})"[:300])
        return CheckResult(check=check.name, ok=code == check.expect, detail=f"HTTP {code} from {check.target}")


class TcpProbe:
    def look(self, check: Check) -> CheckResult:
        host, _, port = check.target.rpartition(":")
        try:
            with socket.create_connection((host, int(port)), timeout=check.timeout):
                return CheckResult(check=check.name, ok=True, detail=f"{check.target} is open")
        except (OSError, ValueError) as error:
            return CheckResult(check=check.name, ok=False, detail=f"{check.target} is closed ({error})"[:300])


class UnitProbe:
    def look(self, check: Check) -> CheckResult:
        argv = ["systemctl", *(["--user"] if check.scope == "user" else []), "is-active", check.target]
        state = run(argv, check.timeout).out.strip() or "unknown"
        return CheckResult(check=check.name, ok=state == "active", detail=f"{check.target} is {state}")


class CommandProbe:
    def look(self, check: Check) -> CheckResult:
        if not check.argv:
            return CheckResult(check=check.name, ok=False, detail="command check has no argv")
        ran = run(check.argv, check.timeout)
        return CheckResult(check=check.name, ok=ran.ok, detail=f"exit {ran.code}: {ran.said}")


class ProbeRegistry:
    def __init__(self) -> None:
        self._probes: dict[str, IProbe] = {}

    def register(self, kind: str, probe: IProbe) -> "ProbeRegistry":
        self._probes[kind] = probe
        return self

    def look(self, check: Check) -> CheckResult:
        probe = self._probes.get(check.kind)
        if probe is None:
            return CheckResult(check=check.name, ok=False, detail=f"no probe for kind '{check.kind}'")
        return probe.look(check)

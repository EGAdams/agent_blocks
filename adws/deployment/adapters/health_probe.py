"""IHealthProbe: an address that answers, a unit that is active, a heartbeat file that is fresh."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

from ..interfaces import IServiceManager
from ..models import HealthCheck


class HealthProbe:
    def __init__(self, services: IServiceManager, timeout: float = 10.0) -> None:
        self._services, self._timeout = services, timeout

    def check(self, check: HealthCheck) -> tuple[bool, str]:
        try:
            return getattr(self, "_" + check.kind)(check)
        except Exception as exc:                      # a probe answers; it never stops the run
            return False, f"{type(exc).__name__}: {str(exc)[:200]}"

    def _http(self, check: HealthCheck) -> tuple[bool, str]:
        request = urllib.request.Request(check.url or "", headers={"User-Agent": "adw-deploy/1"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as answer:
                status, body = answer.status, answer.read(200_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, ""
        if status != check.expect_status:
            return False, f"HTTP {status}, expected {check.expect_status}"
        if check.contains and check.contains not in body:
            return False, f"HTTP {status}, but the page does not contain {check.contains!r}"
        return True, f"HTTP {status}"

    def _unit(self, check: HealthCheck) -> tuple[bool, str]:
        state = self._services.state(check.unit or "")
        return state == "active", state

    def _file_fresh(self, check: HealthCheck) -> tuple[bool, str]:
        path = Path(check.path or "").expanduser()
        if not path.is_file():
            return False, "missing"
        age = int(time.time() - path.stat().st_mtime)
        return age <= check.max_age_seconds, f"written {age}s ago"

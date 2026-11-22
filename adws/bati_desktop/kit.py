"""The only composition root of bati_desktop: names every concrete, and the audit log and alerter beside them."""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browser_control.link import WslInteropLink
from browser_control.models import BROWSERS
from browser_control.windows import InteractiveTaskStarter, WindowsSessionProbe
from browser_session.models import DebugEndpoint

from .interfaces import IAlerter, IAuditLog, IRelay
from .models import BatiPolicy
from .operations import BatiChrome
from .relay import RelayCdpConnector, RelayTargetDirectory, WindowsPythonRelay


DATA = Path(__file__).resolve().parent.parent / "adw_data" / "bati_desktop"
AUDIT = DATA / "audit.jsonl"
SCREENS = DATA / "screens"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class JsonlAudit:
    """IAuditLog: one line per event, appended, flushed. The file is the record; nothing rewrites it."""

    def __init__(self, path: Path = AUDIT) -> None:
        self._path = path

    def write(self, record: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": now(), **record}, default=str) + "\n")


class ToastAlerter:
    """IAlerter: a Windows toast on BATI's own desktop (the Machine Supervisor's PowerShellToast). Never raises."""

    def alert(self, title: str, message: str) -> str:
        try:
            from machine_supervisor.adapters.toast import PowerShellToast
            from machine_supervisor.models import Alert
            return PowerShellToast().show(Alert(title=title[:120], message=message[:600]))
        except Exception as exc:                                    # an alert that failed is reported, not raised
            return f"toast NOT shown ({type(exc).__name__}: {str(exc)[:160]})"


class Kit:
    """The wired services for one run (services, not data -- so not a BaseModel)."""

    def __init__(self, policy: BatiPolicy, chrome: BatiChrome, relay: IRelay, audit: IAuditLog,
                 alerter: IAlerter) -> None:
        self.policy, self.chrome, self.relay, self.audit, self.alerter = policy, chrome, relay, audit, alerter


def build(policy: BatiPolicy | None = None) -> Kit:
    policy = policy or BatiPolicy()
    kind = BROWSERS["chrome"]
    if kind.port != policy.port:
        raise ValueError(f"policy port {policy.port} is not Browser Control's Chrome port {kind.port}")
    link = WslInteropLink()
    ok, why = link.ensure()                                          # exports a working WSL_INTEROP for children
    if not ok:
        raise RuntimeError(why)
    relay = WindowsPythonRelay(policy.windows_python, policy.port, policy.relay_call_seconds, env=dict(os.environ))
    directory = RelayTargetDirectory(relay)
    connector = RelayCdpConnector(relay, DebugEndpoint(port=policy.port), policy.relay_call_seconds)
    chrome = BatiChrome(policy, kind, relay, directory, connector, InteractiveTaskStarter(), WindowsSessionProbe(),
                        socket.gethostname, SCREENS)
    return Kit(policy=policy, chrome=chrome, relay=relay, audit=JsonlAudit(), alerter=ToastAlerter())

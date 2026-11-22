"""What the BATI desktop pieces need from each other, as Protocols. The kit is the only place naming concretes."""
from __future__ import annotations

from typing import Any, Protocol

from browser_control.models import BrowserKind, BrowserPlacement


class IRelay(Protocol):
    """One request/answer exchange with the Windows-side relay. Raises RelayError on refusal or timeout."""

    def request(self, op: str, timeout: float | None = None, **fields: Any) -> dict[str, Any]: ...

    def close(self) -> None: ...


class IAuditLog(Protocol):
    """Append-only record of every attempt: who, what, when, outcome."""

    def write(self, record: dict[str, Any]) -> None: ...


class IAlerter(Protocol):
    """Tells the person at BATI that a run failed or timed out. Returns what happened; never raises."""

    def alert(self, title: str, message: str) -> str: ...


class IStarter(Protocol):
    """Starts the debug Chrome on the signed-in person's desktop (browser_control.windows.InteractiveTaskStarter)."""

    def start(self, kind: BrowserKind, url: str) -> tuple[bool, str]: ...


class ISessions(Protocol):
    """Which Windows session holds the debug Chrome, and which have a desktop (browser_control.windows)."""

    def placement(self, kind: BrowserKind) -> BrowserPlacement: ...


class IHostName(Protocol):
    def __call__(self) -> str: ...

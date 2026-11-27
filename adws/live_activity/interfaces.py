"""One interface per source, so the ADW names no file layout."""
from __future__ import annotations

from typing import Protocol

from .models import LiveEvent


class IAgentEventSource(Protocol):
    def read(self, after: int | None) -> tuple[list[LiveEvent], int]: ...        # events, next cursor


class ISupervisorEventSource(Protocol):
    def read(self) -> tuple[list[LiveEvent], list[str]]: ...                     # events, gaps

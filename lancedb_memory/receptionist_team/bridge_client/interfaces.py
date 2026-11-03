"""The two seams: what Toyota's runner needs, and how bytes reach the gateway."""
from typing import Any, Protocol

from .models import BridgeAgent, BridgeResult, BridgeTaskRequest, BridgeTaskView


class IBridgeTransport(Protocol):
    """One authenticated JSON request. Raises a bridge_client.errors type on failure."""

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        ...


class IBridgeClient(Protocol):
    """The narrow client: discover, submit, status, result. No cancel, no worker API."""

    def discover(self) -> list[BridgeAgent]:
        ...

    def submit(self, request: BridgeTaskRequest) -> BridgeTaskView:
        ...

    def status(self, task_id: str) -> BridgeTaskView:
        ...

    def result(self, task_id: str) -> BridgeResult:
        ...

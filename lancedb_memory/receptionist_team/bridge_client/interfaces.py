"""The two seams: what Toyota's runner needs, and how bytes reach the gateway."""
from typing import Any, Protocol

from .models import (BridgeActionRequest, BridgeAgent, BridgeResult, BridgeTaskRequest, BridgeTaskView,
                     DownloadedFileList, FileView)


class IBridgeTransport(Protocol):
    """One authenticated JSON request. Raises a bridge_client.errors type on failure."""

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        ...


class IBridgeClient(Protocol):
    """The client API: discover, submit, status, result, cancel. Never the worker API."""

    def discover(self) -> list[BridgeAgent]:
        ...

    def submit(self, request: BridgeTaskRequest | BridgeActionRequest) -> BridgeTaskView:
        ...

    def status(self, task_id: str) -> BridgeTaskView:
        ...

    def result(self, task_id: str) -> BridgeResult:
        ...

    def cancel(self, task_id: str) -> BridgeTaskView:
        ...


class IBridgeFiles(Protocol):
    """The files Toyota's own Bridge downloads saved: list them, read one. Text and facts, never images."""

    def downloaded_files(self, task_id: str = "", limit: int = 10) -> DownloadedFileList:
        ...

    def view_file(self, file_id: str, page: int | None = None, text_offset: int = 0) -> FileView:
        ...

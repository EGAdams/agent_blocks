"""BridgeClient -- discover, submit, status, result over any IBridgeTransport.

Every response is validated into a model; anything that doesn't fit is
BridgeMalformed, never a guess. cancel() asks the gateway to stop a task; a
task already running may still finish (the gateway records cancel_requested).
"""
import re

from pydantic import ValidationError

from .errors import BridgeMalformed, BridgeRefused
from .interfaces import IBridgeTransport
from .models import (TASK_ID_PATTERN, BridgeActionRequest, BridgeAgent, BridgeAgentList, BridgeResult,
                     BridgeTaskRequest, BridgeTaskView)

_TASK_ID = re.compile(TASK_ID_PATTERN)


def _check_task_id(task_id: str) -> str:
    if not _TASK_ID.match(task_id or ""):
        raise BridgeRefused("bad_task_id", "that is not a Bridge task id")
    return task_id


class BridgeClient:
    """IBridgeClient over an injected transport."""

    def __init__(self, transport: IBridgeTransport) -> None:
        self._transport = transport

    def _parse(self, model, data, what: str):
        try:
            return model.model_validate(data)
        except ValidationError:
            raise BridgeMalformed("malformed", f"the Bridge's {what} did not match the contract") from None

    def discover(self) -> list[BridgeAgent]:
        _, data = self._transport.request("GET", "/agents")
        return list(self._parse(BridgeAgentList, data, "agent list").agents)

    def submit(self, request: BridgeTaskRequest | BridgeActionRequest) -> BridgeTaskView:
        status, data = self._transport.request("POST", "/tasks", request.body())
        if status not in (200, 201):
            raise BridgeMalformed("unexpected_status", f"submit answered HTTP {status}", status)
        view = self._parse(BridgeTaskView, data, "task")
        if (view.agent, view.workspace, view.operation) != (request.agent, request.workspace, request.operation):
            raise BridgeMalformed("mismatch", "the Bridge recorded a different task than the one sent")
        return view

    def status(self, task_id: str) -> BridgeTaskView:
        _, data = self._transport.request("GET", f"/tasks/{_check_task_id(task_id)}")
        view = self._parse(BridgeTaskView, data, "task")
        if view.task_id != task_id:
            raise BridgeMalformed("mismatch", "the Bridge answered about a different task")
        return view

    def result(self, task_id: str) -> BridgeResult:
        _, data = self._transport.request("GET", f"/tasks/{_check_task_id(task_id)}/result")
        result = self._parse(BridgeResult, data, "result")
        if result.task_id != task_id:
            raise BridgeMalformed("mismatch", "the Bridge answered about a different task")
        return result

    def cancel(self, task_id: str) -> BridgeTaskView:
        _, data = self._transport.request("POST", f"/tasks/{_check_task_id(task_id)}/cancel", {})
        view = self._parse(BridgeTaskView, data, "task")
        if view.task_id != task_id:
            raise BridgeMalformed("mismatch", "the Bridge answered about a different task")
        return view

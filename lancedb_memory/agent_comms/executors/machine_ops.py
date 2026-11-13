"""ITaskExecutor adapter for the reviewed Machine Ops command catalog."""
import asyncio
import json

from agent_bridge_worker.senders import APPROVED_BRIDGE_SENDERS

from ..models import Envelope, TaskResult
from ..machine_ops.commands import Request, command
from ..machine_ops.runner import ICommandRunner, SubprocessCommandRunner


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate request field")
        data[key] = value
    return data


class MachineOpsExecutor:
    def __init__(self, runner: ICommandRunner | None = None) -> None:
        self._runner = runner or SubprocessCommandRunner()

    @property
    def performer(self) -> str:
        return "machine_ops:fixed_commands"

    async def execute(self, envelope: Envelope) -> TaskResult:
        if (envelope.sender not in APPROVED_BRIDGE_SENDERS
                or envelope.recipient != "machine-ops" or envelope.task_type != "inspect"):
            return TaskResult(ok=False, text="Machine Ops sender or operation refused.", performer=self.performer)
        try:
            if len(envelope.text.encode()) > 4096:
                raise ValueError("oversized request")
            request = Request.model_validate(json.loads(envelope.text, object_pairs_hook=_unique))
            spec = command(request)
        except (ValueError, TypeError) as error:
            # Same wording as before, plus which rule failed (never the request itself) and no retry:
            # a refused request is refused every time, so repeating it only delays the real error.
            why = "not valid JSON" if isinstance(error, json.JSONDecodeError) else str(error).split("\n")[0][:80]
            return TaskResult(ok=False, text="Machine Ops refused the request: use strict JSON with an approved operation, unit and optional lines or suite. Restarts are disabled. "
                                              f"Reason: {why}.", performer=self.performer, details={"retryable": False})
        try:
            result = await asyncio.to_thread(self._runner.run, spec)
            data = {"operation": request.operation, "unit": request.unit, "suite": request.suite,
                    **result.model_dump(), "restart_enabled": False}
            ok = result.returncode == 0 and not result.timed_out and not result.truncated
            return TaskResult(ok=ok, text=json.dumps({"ok": ok, **data}), details=data, performer=self.performer)
        except Exception:
            return TaskResult(ok=False, text="Machine Ops command unavailable; no restart attempted.", performer=self.performer)

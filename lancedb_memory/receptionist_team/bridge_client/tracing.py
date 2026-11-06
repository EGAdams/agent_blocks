"""TracingBridgeClient -- every Toyota submission to the Bridge, as a traced object.

Decorator over IBridgeClient. A submit records a `toyota.bridge_submit` object
carrying the gateway's task id and the turn it came from (the run_id that
receptionist_team.object_tracing put in scope), which is how the inspector
answers "Toyota submitted it in turn ..." and tells a Toyota task from one
ChatGPT or a direct client sent. Only the agent, workspace, operation, approval
mode and idempotency key are recorded, never the prompt.

The object is begun after the gateway answers, because only then is the task
id known; a submission that raises is recorded as failed with no task id.
Reads (discover/status/result/cancel) pass straight through.
"""
import time
from collections.abc import Callable

from .interfaces import IBridgeClient
from .models import BridgeActionRequest, BridgeAgent, BridgeResult, BridgeTaskRequest, BridgeTaskView

SUBMIT_KIND = "toyota.bridge_submit"


def _default_tracer():
    from object_trace.factory import default_tracer
    return default_tracer()


class TracingBridgeClient:
    def __init__(self, inner: IBridgeClient, tracer_factory: Callable[[], object] = _default_tracer,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._inner, self._tracer_factory, self._clock = inner, tracer_factory, clock

    def discover(self) -> list[BridgeAgent]:
        return self._inner.discover()

    def submit(self, request: BridgeTaskRequest | BridgeActionRequest) -> BridgeTaskView:
        started = self._clock()
        try:
            view = self._inner.submit(request)
        except Exception as exc:
            self._record(request, None, started, error=exc)
            raise
        self._record(request, view, started)
        return view

    def status(self, task_id: str) -> BridgeTaskView:
        return self._inner.status(task_id)

    def result(self, task_id: str) -> BridgeResult:
        return self._inner.result(task_id)

    def cancel(self, task_id: str) -> BridgeTaskView:
        return self._inner.cancel(task_id)

    def _record(self, request, view: BridgeTaskView | None, started: float, error: Exception | None = None) -> None:
        try:
            tracer = self._tracer_factory()
            inputs = {"agent": request.agent, "workspace": request.workspace, "operation": request.operation,
                      "approval_mode": request.approval_mode, "idempotency_key": request.idempotency_key}
            object_id = f"{SUBMIT_KIND}:{view.task_id}" if view is not None else None
            trace = tracer.begin(SUBMIT_KIND, object_id, interface="IBridgeClient",
                                 implementation=type(self._inner).__name__, module=type(self._inner).__module__,
                                 label=f"submit {request.agent} {request.operation}", inputs=inputs,
                                 task_id=view.task_id if view is not None else None)
            seconds = round(self._clock() - started, 3)
            if error is not None:
                trace.fail(error, data={"seconds": seconds})
            else:
                trace.succeed({"task_id": view.task_id, "state": view.state, "replayed": view.replayed,
                               "seconds": seconds}, f"submitted {view.task_id} ({view.state})")
        except Exception:  # noqa: BLE001 -- telemetry never changes what the submission returned or raised
            pass

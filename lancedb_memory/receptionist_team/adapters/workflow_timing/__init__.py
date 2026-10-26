"""Content-free timing Decorator over the workflow boundary."""
from agent_memory.adapters.turn_telemetry import timed_stage

from ...operations.interfaces import Workflows
from ...operations.models import WorkIntent, WorkReply, WorkView


class TimedWorkflows:
    def __init__(self, inner: Workflows) -> None:
        self._inner = inner

    async def state(self) -> WorkView:
        with timed_stage('workflow_state'):
            return await self._inner.state()

    async def execute(self, intent: WorkIntent, request_id: str | None = None) -> WorkReply:
        with timed_stage('workflow_operation'):
            return await self._inner.execute(intent, request_id=request_id)

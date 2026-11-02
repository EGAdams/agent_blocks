"""Run Scala's answer ADW; return the line it wrote, unchanged."""
import asyncio

from agent_memory.models import AgentReply

from ..adw import IAdwWorkflow
from .adw_process import SpokenAdwFailure, SubprocessAdwWorkflow


class ScalaAnswerRunner:
    def __init__(self, workflow: IAdwWorkflow | None = None) -> None:
        self._workflow = workflow if workflow is not None else SubprocessAdwWorkflow(
            "adw_interface_scala_answer.py",
            SpokenAdwFailure("Scala's filing questions", "file that scan"),
            timeout_seconds=60,
        )

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        if not extra.strip():
            return AgentReply(text="I didn't catch which folder Scala should use, so nothing was filed.")
        line = await asyncio.to_thread(self._workflow.line, "answer scala", ["--request", extra])
        return AgentReply(text=line)

"""Run the company to-do ADW; return the line it wrote, unchanged."""
import asyncio

from agent_memory.models import AgentReply

from ..adw import IAdwWorkflow
from .adw_process import SpokenAdwFailure, SubprocessAdwWorkflow


class CompanyTodoRunner:
    def __init__(self, workflow: IAdwWorkflow | None = None, who: str = "toyota") -> None:
        self._workflow = workflow if workflow is not None else SubprocessAdwWorkflow(
            "adw_interface_company_todos.py",
            SpokenAdwFailure("the company to-do list", "answer from the list"),
            timeout_seconds=60,
        )
        self._who = who

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        if not extra.strip():
            return AgentReply(text="I didn't catch what to do with the to-do list, so I left it alone.")
        line = await asyncio.to_thread(
            self._workflow.line, f"{self._who} to-do request", ["--request", extra, "--by", self._who])
        return AgentReply(text=line)

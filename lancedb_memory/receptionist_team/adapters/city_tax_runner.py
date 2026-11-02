"""Run Liz's desk ADW (adws/adw_liz_ask.py); return the line it wrote, unchanged."""
import asyncio

from agent_memory.models import AgentReply

from ..adw import IAdwWorkflow
from .adw_process import SpokenAdwFailure, SubprocessAdwWorkflow


class LizDeskRunner:
    def __init__(self, workflow: IAdwWorkflow | None = None) -> None:
        # Preparing the return from a cold start reads two PDFs by OCR; give it room.
        self._workflow = workflow if workflow is not None else SubprocessAdwWorkflow(
            "adw_liz_ask.py",
            SpokenAdwFailure("Liz's city tax desk", "work on the city return"),
            timeout_seconds=1500,
        )

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        if not extra.strip():
            return AgentReply(text="I didn't catch what you'd like me to do with the city return.")
        line = await asyncio.to_thread(self._workflow.line, "ask liz", ["--request", extra])
        return AgentReply(text=line)

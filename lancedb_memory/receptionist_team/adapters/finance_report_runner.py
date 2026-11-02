"""Run the existing report build; return its actual publication result unchanged."""
import asyncio
import re

from agent_memory.models import AgentReply

from ..adw import IAdwWorkflow
from .adw_process import SpokenAdwFailure, SubprocessAdwWorkflow


class FinanceReportRunner:
    def __init__(self, workflow: IAdwWorkflow | None = None) -> None:
        self._workflow = workflow if workflow is not None else SubprocessAdwWorkflow(
            "adw_interface_finance_report.py",
            SpokenAdwFailure("the finance report workflow", "published finance report"),
            timeout_seconds=540,
        )

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        # This ADW is specifically the 2025 report, including its SQL and
        # workbook. Never silently answer a different year's request with it.
        years = set(re.findall(r"\b20\d{2}\b", extra))
        if years - {"2025"}:
            return AgentReply(text="This workflow builds the River of Life nonprofit 2025 finance report only. No report was generated for the other year you named.")
        if re.search(r"\b(personal|business|for[ -]profit)\b", extra, re.I):
            return AgentReply(text="This workflow builds the River of Life nonprofit finance report only. No report was generated for the other books you named.")
        line = await asyncio.to_thread(self._workflow.line, extra, [])
        return AgentReply(text=line)

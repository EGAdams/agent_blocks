"""A report generation request reaches the installed workflow, before the model."""
import re

from .latest_user import latest_user_message

_REPORT = re.compile(r"\bfinanc(?:e|ial)\s+reports?\b", re.I)
_ACTION = re.compile(r"\b(creat\w*|generat\w*|genterat\w*|build\w*|make|refresh\w*|rebuild\w*|update\w*|run|publish\w*)\b", re.I)
_DISCUSSION = re.compile(r"\b(how (?:to|do|does|can|would)|(?:do|does) .{0,25}know how|explain|why|don't|do not|never|stop|cancel)\b", re.I)


class FinanceReportRoute:
    def forced_agent(self, prompt: str) -> str | None:
        message = latest_user_message(prompt)
        if _REPORT.search(message) and _ACTION.search(message) and not _DISCUSSION.search(message):
            return "finance_report"
        return None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)

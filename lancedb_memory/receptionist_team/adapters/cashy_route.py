"""CashyR46Route -- a request for Cashy on R46 goes to bridge_cashy, never to the dashboard's Cashy window.

2026-11-17: asked "cashy-r46@cashy-r46: {"cashy":"status",...}" Sydney opened Cashy's dashboard window (the
local Cashy on SQMC, :8770) instead, and asked "Ask Cashy on R46 for her session status" she sent a general
read-only question to codex, which described Cashy's state rather than asking her. Cashy on R46 is a Bridge agent
(`cashy-r46`, R46's own worker); only the bridge_cashy specialist can send to it, and its changes wait for a yes.

So this fires on the latest user message only, when it names Cashy together with R46 (Rosemary46, Mom's computer),
or the `cashy-r46` agent itself. "Open Cashy" with no machine named is left alone: that is the dashboard window.
"""
import re

from .latest_user import latest_user_message

_CASHY = re.compile(r"\bcashy\b", re.I)
_R46 = re.compile(r"\b(?:r46|rosemary\s?46)\b|\bmom'?s\s+(?:computer|machine|pc)\b", re.I)
_AGENT = re.compile(r"\bcashy-r46\b", re.I)


def cashy_r46_request(text: str) -> bool:
    if not text:
        return False
    return bool(_AGENT.search(text) or (_CASHY.search(text) and _R46.search(text)))


class CashyR46Route:
    def __init__(self, agent_name: str = "bridge_cashy") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if cashy_r46_request(latest_user_message(prompt)) else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)

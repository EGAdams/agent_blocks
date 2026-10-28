"""AgentHandoffModel -- Decorator that lets Toyota open another agent's window.

Same shape as RoutingModel: wraps a LanguageModel, returns a LanguageModel.
A whole-reply `OPEN_AGENT: <name>` directive becomes a spoken sentence plus a
handoff event for the browser, which opens that agent's page in its own
window. Nothing runs here -- the browser does the opening -- so this is safe
to match on every turn.
"""
import re
from collections.abc import Callable

from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

# Agents the dashboard can open, keyed by the name Toyota uses in the directive.
# Toyota opens Mazda's or Sydney's; Sydney opens Toyota's (the front desk, "/").
# The dashboard's AGENT_WINDOWS (js/implementation/agent-window-handoff.js)
# must name every key here, or the browser ignores the hand-off.
HANDOFF_AGENTS: dict[str, str] = {"mazda": "Mazda", "sydney": "Sydney", "toyota": "Toyota"}

# Whole reply only (re.fullmatch), like _ROUTE_RE: conversation may mention it.
_OPEN_AGENT_RE = re.compile(r"OPEN_AGENT:\s*([A-Za-z_]+)\s*(?:\|\s*(.*))?", re.DOTALL)


class AgentHandoffModel:
    """If the inner reply is an OPEN_AGENT directive, emit a handoff and speak instead."""

    def __init__(
        self,
        inner: LanguageModel,
        on_handoff: Callable[[dict], None] | None = None,
    ) -> None:
        self._inner = inner
        # service.py passes a sink that streams {"agent": ...} to the browser;
        # the REPL leaves this None because it has no window to open.
        self._on_handoff = on_handoff

    async def complete(self, prompt: str) -> AgentReply:
        reply = await self._inner.complete(prompt)
        match = _OPEN_AGENT_RE.fullmatch(reply.text.strip())
        if not match:
            return reply

        key = match.group(1).lower()
        spoken = (match.group(2) or "").strip()
        name = HANDOFF_AGENTS.get(key)
        if name is None:
            return reply.model_copy(update={
                "text": f"I don't have a window for {match.group(1)}. I can open: "
                        f"{', '.join(HANDOFF_AGENTS.values())}."
            })
        if self._on_handoff is None:
            return reply.model_copy(update={
                "text": f"I can only open {name}'s window from the dashboard."
            })

        self._on_handoff({"agent": key, "name": name})
        return reply.model_copy(update={
            "text": spoken or f"Opening {name}'s window now. Ask her to put me on the horn when you want me back."
        })

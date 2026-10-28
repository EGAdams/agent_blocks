"""PostOfficeRunner -- an IDelegationRunner that sends the request through agent_comms.

This is how a receptionist reaches Sonnet, Opus, the mechanic or the other
receptionist for real work. It does not run a model to decide anything: the
user's words go to the post office's own operations -- `delegate_task`
(route -> validate -> send -> capture), `task_status`, `cancel_task` -- and
the reply is the line those operations produce, phrased in the
receptionist's voice.

It replaces nothing. ask_sonnet / ask_opus still answer read-only questions
with their run-record context; this is the door for work. Before it existed
the only door to Sonnet was ask_sonnet, whose dispatcher was told to stop when
"there is no actual question", which is how a build request ended in "What
would you like me to actually ask Sonnet?" with nothing ever sent.

agent_comms is imported on first use, the same deferred-import posture as
profiles.py's runner factories: building a receptionist must not drag it in.
"""
import asyncio
import re
from collections.abc import Callable

from agent_memory.models import AgentReply

# Replies are spoken; the full text stays on the post office's board.
SPOKEN_LIMIT = 700

_STATUS = re.compile(
    r"^\s*(what('s| is) the )?(status|progress)\b|\b(my|our|the) tasks?\b.*\b(status|done|doing)\b|"
    r"^\s*(is|has) .* (done|finished)\b|\bhow is .* going\b", re.IGNORECASE)
_CANCEL = re.compile(r"\bcancel\b.*?\b(task\s+)?(?P<id>\d{8}T\d{6}Z_[0-9a-f]{8}_from_[a-z-]+)", re.IGNORECASE)


def _default_kit():
    from agent_comms.factory import build_post_office
    return build_post_office()


class PostOfficeRunner:
    """Sends a receptionist's delegated request through the post office."""

    def __init__(self, sender: str, kit_factory: Callable[[], object] = _default_kit) -> None:
        self._sender = sender
        self._kit_factory = kit_factory
        self._kit_instance = None
        self._operations = None

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        text = (extra or "").strip()
        if not text:
            return AgentReply(text="Tell me who it's for and what you need, and I'll send it.")
        return AgentReply(text=await asyncio.to_thread(self._handle, text))

    def _handle(self, text: str) -> str:
        ops = self._ops()
        cancel = _CANCEL.search(text)
        if cancel:
            return ops["cancel_task"].run({"task_id": cancel.group("id"), "sender": self._sender}).line
        if _STATUS.search(text) and not self._names_an_agent(text):
            return ops["task_status"].run({"sender": self._sender, "limit": 3}).line
        outcome = ops["delegate_task"].run({"sender": self._sender, "text": text})
        return self._spoken(outcome)

    def _spoken(self, outcome) -> str:
        data = outcome.data
        if not outcome.ok:
            # Refusals and failures are the post office's own words: they name
            # the actual reason (no such agent, not on its card, a loop).
            return _clip(outcome.line)
        who = self._display(data.get("recipient", "them"))
        task = data.get("task_id", "")
        if data.get("asynchronous"):
            return (f"I've sent that to {who} as task {_short(task)}. {who} is working on it now; "
                    "ask me for the status any time and I'll tell you how it went.")
        if data.get("pending"):
            return (f"{who} hasn't answered yet on task {_short(task)}. Nothing is lost: "
                    "the answer will be kept, so ask me for the status in a bit.")
        return _clip(f"{who} says: {outcome.line}")

    def _names_an_agent(self, text: str) -> bool:
        return self._kit().router.route(self._sender, text) is not None

    def _display(self, name: str) -> str:
        card = self._kit().registry.card(name)
        return card.display_name if card else name

    def _kit(self):
        if self._kit_instance is None:
            self._kit_instance = self._kit_factory()
        return self._kit_instance

    def _ops(self):
        if self._operations is None:
            from agent_comms.operations.catalog import operations
            self._operations = operations(self._kit())
        return self._operations


def _short(task_id: str) -> str:
    """The spoken handle: the 8-hex part of the id, which is unique enough to say."""
    parts = task_id.split("_")
    return parts[1] if len(parts) > 1 else task_id


def _clip(text: str) -> str:
    return text if len(text) <= SPOKEN_LIMIT else text[:SPOKEN_LIMIT].rsplit(" ", 1)[0] + " ..."

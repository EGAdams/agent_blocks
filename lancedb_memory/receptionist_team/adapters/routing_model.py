"""RoutingModel -- Decorator that lets a receptionist delegate a turn to one
of her specialists.

Same shape as agent_memory/adapters/decorators.py's RetryingModel: wraps a
LanguageModel, returns a LanguageModel. agent.py and ConversationAgent never
know a delegation happened -- they see one complete() call, one AgentReply.

Nothing here is Toyota-specific any more. Who the specialists are comes from
the injected Roster, how they run comes from the injected IDelegationRunner,
and whose name appears in the console line comes from `speaker`. Toyota passes
TOYOTA_ROSTER + ToolAgentRunner; Mazda passes MAZDA_ROSTER + UnwiredRunner.
"""
import re
from collections.abc import Callable
from dataclasses import replace

from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from ..delegation import IDelegationRunner
from ..rosters import Roster

# Must match the WHOLE reply (re.fullmatch), not just contain the word ROUTE --
# ordinary conversation is allowed to mention routing without triggering it.
_ROUTE_RE = re.compile(r"ROUTE:\s*([A-Za-z_]+)\s*(?:\|\s*(.*))?", re.DOTALL)


class RoutingModel:
    """If the inner model's reply is a bare ROUTE directive, run that
    specialist to completion and hand its result back as the reply.
    Anything else passes through untouched -- ordinary conversation never
    goes near a specialist or the live database.
    """

    def __init__(
        self,
        inner: LanguageModel,
        model: str,
        roster: Roster,
        runner: IDelegationRunner,
        speaker: str = "Toyota",
        on_delegation: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._role_model = model
        self._roster = roster
        self._runner = runner
        self._speaker = speaker
        # Called with the spoken acknowledgment the moment a ROUTE line is
        # matched, BEFORE the slow runner call -- which is what the system
        # prompt has always promised ("the application supplies the spoken
        # acknowledgment separately so that it can begin playback immediately
        # while the delegated agent starts working"). The CLI leaves this None
        # and just prints; service.py passes a sink that streams the sentence
        # so the browser can start speaking it.
        self._on_delegation = on_delegation

    async def complete(self, prompt: str) -> AgentReply:
        reply = await self._inner.complete(prompt)
        match = _ROUTE_RE.fullmatch(reply.text.strip())
        if not match:
            return reply

        agent_name, extra = match.group(1), (match.group(2) or "").strip()
        if not self._roster.has(agent_name):
            return AgentReply(text=self._roster.unknown_agent_reply(agent_name))

        role = self._roster.role(agent_name)
        if extra:
            role = replace(
                role,
                task_prompt=(
                    f"{role.task_prompt}\n\nAdditional instructions relayed by "
                    f"the receptionist from the user: {extra}"
                ),
            )

        self._acknowledge(agent_name)
        return await self._runner.run(role, agent_name, self._role_model, extra)

    def _acknowledge(self, agent_name: str) -> None:
        """Say "please wait" now, before the slow call -- never after it.

        A broken sink must not cost the user the delegated work that was about
        to run, so a failure here is reported and stepped over.
        """
        acknowledgment = self._roster.acknowledgment(agent_name)
        print(f"\n[{self._speaker} -> {agent_name}] {acknowledgment}")
        if self._on_delegation is None:
            return
        try:
            self._on_delegation(acknowledgment)
        except Exception as exc:
            print(f"[acknowledgment playback failed: {type(exc).__name__}: {exc}]")

"""RoutingModel -- Decorator that lets Toyota delegate a turn to one of
finance_team's headless roles.

Same shape as agent_memory/adapters/decorators.py's RetryingModel: wraps a
LanguageModel, returns a LanguageModel. agent.py and ConversationAgent never
know a delegation happened -- they see one complete() call, one AgentReply.
"""
import re
from collections.abc import Callable
from dataclasses import replace

from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from finance_team.adapters.tool_agent import run_role
from finance_team.roles import REPO_ROOT, ROLES

from ..roles import delegation_acknowledgment

# Must match the WHOLE reply (re.fullmatch), not just contain the word ROUTE --
# ordinary conversation is allowed to mention routing without triggering it.
_ROUTE_RE = re.compile(r"ROUTE:\s*([A-Za-z_]+)\s*(?:\|\s*(.*))?", re.DOTALL)


class RoutingModel:
    """If the inner model's reply is a bare ROUTE directive, run that
    finance_team role to completion and hand its result back as the reply.
    Anything else passes through untouched -- Toyota's ordinary conversation
    never goes near finance_team or the live database.
    """

    def __init__(
        self,
        inner: LanguageModel,
        model: str,
        on_delegation: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._role_model = model
        # Called with the spoken acknowledgment the moment a ROUTE line is
        # matched, BEFORE the slow run_role call -- which is what
        # TOYOTA_SYSTEM_PROMPT has always promised ("the application supplies
        # the spoken acknowledgment separately so that it can begin playback
        # immediately while the delegated agent starts working"). The CLI
        # leaves this None and just prints; service.py passes a sink that
        # streams the sentence so the browser can start speaking it.
        self._on_delegation = on_delegation

    async def complete(self, prompt: str) -> AgentReply:
        reply = await self._inner.complete(prompt)
        match = _ROUTE_RE.fullmatch(reply.text.strip())
        if not match:
            return reply

        role_name, extra = match.group(1), (match.group(2) or "").strip()
        role = ROLES.get(role_name)
        if role is None:
            return AgentReply(
                text=(
                    f'I tried to route that to "{role_name}", but I don\'t have '
                    f"an agent by that name. I know: {', '.join(sorted(ROLES))}."
                )
            )

        if extra:
            role = replace(
                role,
                task_prompt=(
                    f"{role.task_prompt}\n\nAdditional instructions relayed by "
                    f"the receptionist from the user: {extra}"
                ),
            )

        acknowledgment = delegation_acknowledgment(role_name)
        print(f"\n[Toyota -> {role_name}] {acknowledgment}")
        if self._on_delegation is not None:
            self._on_delegation(acknowledgment)

        result = await run_role(role, cwd=REPO_ROOT, model=self._role_model)

        if result.is_error:
            text = f"I checked with {role_name}, but it hit a problem: {result.final_text}"
        else:
            text = f"I checked with {role_name}: {result.final_text}"
        return AgentReply(text=text, cost_usd=result.cost_usd)

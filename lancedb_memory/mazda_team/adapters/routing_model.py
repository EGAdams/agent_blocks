"""RoutingModel -- Decorator that lets Mazda delegate a turn to one of
mazda_team's headless roles.

Same shape as agent_memory/adapters/decorators.py's RetryingModel: wraps a
LanguageModel, returns a LanguageModel. agent.py and ConversationAgent never
know a delegation happened -- they see one complete() call, one AgentReply.
"""
import re
from dataclasses import replace
from pathlib import Path

from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from ..specialist_roles import REPO_ROOT, ROLES
from .tool_agent import run_role

# Must match the WHOLE reply (re.fullmatch), not just contain the word ROUTE --
# ordinary conversation is allowed to mention routing without triggering it.
_ROUTE_RE = re.compile(r"ROUTE:\s*([A-Za-z_]+)\s*(?:\|\s*(.*))?", re.DOTALL)


class RoutingModel:
    """If the inner model's reply is a bare ROUTE directive, run that
    mazda_team role to completion and hand its result back as the reply.
    Anything else passes through untouched -- Mazda's ordinary conversation
    never goes near a specialist.
    """

    def __init__(self, inner: LanguageModel, model: str) -> None:
        self._inner = inner
        self._role_model = model

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

        print(f"\n[Mazda -> {role_name}] delegating -- this can take a while...")
        Path(REPO_ROOT).mkdir(parents=True, exist_ok=True)
        result = await run_role(role, cwd=REPO_ROOT, model=self._role_model)

        if result.is_error:
            text = f"I checked with {role_name}, but it hit a problem: {result.final_text}"
        else:
            text = f"I checked with {role_name}: {result.final_text}"
        return AgentReply(text=text, cost_usd=result.cost_usd)

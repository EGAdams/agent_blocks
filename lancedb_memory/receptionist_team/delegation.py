"""IDelegationRunner -- how a receptionist's ROUTE line actually gets run.

Toyota's specialists are real finance_team roles executed through the Claude
Agent SDK against a live database. Mazda's are not connected to anything yet.
That difference is the only thing that varies between the two receptionists at
delegation time, so it is the one thing injected: RoutingModel matches the
control line, says the acknowledgment, and hands the role to a runner.

Interfaces here are `typing.Protocol` and carry no SDK import, matching
agent_memory/ports.py's posture -- adapters/tool_agent_runner.py is where the
Claude-facing implementation lives.
"""
from typing import Protocol

from agent_memory.models import AgentReply


class IDelegationRunner(Protocol):
    """Run one delegated role to completion and phrase the result as a reply.

    Phrasing belongs to the runner, not to RoutingModel: "I checked with the
    receipt scanner and ..." only makes sense when somebody was actually
    checked with, and a stub has to say something honestly different.
    """

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply: ...


class UnwiredRunner:
    """Says plainly that an agent is not connected yet. Runs nothing.

    Mazda's specialists are real Letta agents that this package cannot reach
    yet. Routing to one must not pretend work happened -- Mazda's own standing
    rule is to never claim something was verified, stored or reported unless
    the tools confirm it, and never to say she is working in the background
    when nothing is running. So this returns a sentence that says exactly that,
    and costs nothing.
    """

    def __init__(self, spoken_names: dict[str, str] | None = None) -> None:
        # Spoken names so the refusal still sounds like a colleague ("our
        # statement parser") rather than reading out an internal agent id.
        self._spoken_names = spoken_names or {}

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        who = self._spoken_names.get(agent_name, agent_name)
        return AgentReply(
            text=(
                f"I'd normally hand that to {who}, but that connection isn't "
                "built yet, so nothing has run and nothing has changed. "
                "I didn't want to tell you it was done when it wasn't."
            )
        )


class RunnerMap:
    """Dispatch by agent name, with one default for everybody else.

    One receptionist can have specialists that run in different ways --
    Michi's progress answer is a local snapshot plus one explaining model
    call, while her other specialists are headless tool agents (or, for now,
    not connected at all). RoutingModel shouldn't know that: it asks one
    runner, and this is the one that forwards.
    """

    def __init__(self, runners: dict[str, IDelegationRunner], default: IDelegationRunner) -> None:
        self._runners = dict(runners)
        self._default = default

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        runner = self._runners.get(agent_name, self._default)
        return await runner.run(role, agent_name, model, extra)

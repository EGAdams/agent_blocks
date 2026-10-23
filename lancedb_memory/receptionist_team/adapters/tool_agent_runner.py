"""ToolAgentRunner -- the IDelegationRunner that actually runs a finance role.

This is the only module in receptionist_team that reaches into
finance_team.adapters.tool_agent, which is the module allowed to import
claude_agent_sdk. Keeping the import here rather than in routing_model.py is
what lets Mazda reuse RoutingModel without dragging the Claude SDK and the
rol_finances repo root in behind her.
"""
from agent_memory.models import AgentReply

from finance_team.adapters.tool_agent import run_role
from finance_team.roles import REPO_ROOT


class ToolAgentRunner:
    """Run a role headless through the Claude Agent SDK, in a given directory."""

    def __init__(self, cwd: str | None = None) -> None:
        # Defaults to finance_team's REPO_ROOT -- the real rol_finances
        # checkout these roles operate on (see finance_team/roles.py, the
        # deliberate path-hardcoding exception).
        self._cwd = cwd or REPO_ROOT

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        # `extra` is ignored here on purpose: RoutingModel has already merged
        # it into a replace()d copy of role.task_prompt, which is how a tool
        # agent receives instructions. Runners that aren't prompt-driven --
        # MichiRunner -- need the raw text instead, hence the parameter.
        result = await run_role(role, cwd=self._cwd, model=model)
        # is_error becomes spoken text rather than an exception: the user asked
        # a question out loud and deserves an answer either way.
        if result.is_error:
            text = f"I checked with {agent_name}, but it hit a problem: {result.final_text}"
        else:
            text = f"I checked with {agent_name}: {result.final_text}"
        return AgentReply(text=text, cost_usd=result.cost_usd)

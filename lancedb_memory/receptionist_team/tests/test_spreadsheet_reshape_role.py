"""The spreadsheet-reshape specialist: pivots a combined expense spreadsheet
into one column per person.

finance_team/roles.py is locked, so this role can't live in finance_team's
ROLES dict. Instead it's defined in receptionist_team/extra_roles.py and
merged into receptionist_team.roles.ROLES, which adapters/routing_model.py
now reads (instead of finance_team.roles.ROLES directly) so the merged name
actually resolves at dispatch time.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_spreadsheet_reshape_role
"""
from agent_memory.models import AgentReply

from ..adapters import routing_model as routing_model_module
from ..adapters import tool_agent_runner as tool_agent_runner_module
from ..roles import (
    ROLE_BLURBS,
    ROLES,
    TOYOTA_ROSTER,
    TOYOTA_SYSTEM_PROMPT,
    delegation_acknowledgment,
)


async def test_the_role_exists_with_file_tools_and_never_overwrites_the_input():
    role = ROLES["spreadsheet_reshaper"]
    assert {"Bash", "Read", "Write"} <= set(role.allowed_tools)
    assert "person" in role.task_prompt.lower()
    assert "never overwrite" in role.task_prompt.lower()


async def test_the_role_asks_rather_than_guessing_the_input_file():
    prompt = ROLES["spreadsheet_reshaper"].task_prompt
    assert "do not guess" in prompt.lower() and "ask" in prompt.lower()


async def test_toyota_knows_when_to_route_to_it():
    assert "spreadsheet_reshaper" in TOYOTA_SYSTEM_PROMPT
    assert "ROUTE: spreadsheet_reshaper" in TOYOTA_SYSTEM_PROMPT
    assert "spreadsheet_reshaper" in ROLE_BLURBS
    assert "spreadsheet_reshaper" not in delegation_acknowledgment("spreadsheet_reshaper")


class _FakeRouteOnceModel:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, prompt: str) -> AgentReply:
        return AgentReply(text=self._text)


async def test_routing_model_resolves_the_new_specialist_by_name():
    """Proves the merge actually wires up dispatch, not just prompt text --
    without this, ROUTE: spreadsheet_reshaper would hit RoutingModel's
    "I don't have an agent by that name" fallback forever."""
    calls = {}

    async def fake_run_role(role, cwd, model, echo=True):
        calls["role_name"] = role.name

        class _Result:
            is_error = False
            final_text = "reshaped 12 rows into 3 person columns"
            cost_usd = 0.0

        return _Result()

    # run_role moved out of routing_model.py into the ToolAgentRunner --
    # RoutingModel now only matches the control line and hands the role to
    # an injected IDelegationRunner. The SDK call is still what's faked.
    original = tool_agent_runner_module.run_role
    tool_agent_runner_module.run_role = fake_run_role
    try:
        model = routing_model_module.RoutingModel(
            _FakeRouteOnceModel(
                "ROUTE: spreadsheet_reshaper | reshape readable_documents/reports/combined.csv, person column is payee"
            ),
            model="fake-role-model",
            roster=TOYOTA_ROSTER,
            runner=tool_agent_runner_module.ToolAgentRunner(),
        )
        reply = await model.complete("please reshape that spreadsheet")
    finally:
        tool_agent_runner_module.run_role = original

    assert calls.get("role_name") == "spreadsheet_reshaper"
    assert "don't have an agent" not in reply.text
    assert "reshaped 12 rows" in reply.text


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

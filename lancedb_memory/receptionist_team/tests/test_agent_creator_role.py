"""The agent creator: builds new agents for the team, and the ADWs they drive.

Defined as a folder under agents/agent_creator and registered through
extra_roles.py, same as spreadsheet_reshaper -- see
test_spreadsheet_reshape_role.py for the pattern this follows.

The assertions worth having here are the ones that catch a silent regression:
that the role resolves at dispatch, that its prompts still carry the
absolute-path and verification-gate instructions that make it safe (both are
load-bearing, see agents/agent_creator/README.md), and that Toyota knows to
route a "build me an agent" request here rather than to the mechanic.
Sydney owns agent creation now; Toyota is the finance desk and hands it off.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_agent_creator_role
"""
from agent_memory.models import AgentReply

from ..adapters import routing_model as routing_model_module
from ..adapters import tool_agent_runner as tool_agent_runner_module
from ..roles import ROLE_BLURBS, ROLES, TOYOTA_ROSTER
from ..sydney_roles import SYDNEY_ROSTER, SYDNEY_SYSTEM_PROMPT, delegation_acknowledgment


async def test_the_role_exists_with_the_tools_it_needs_to_write_code():
    role = ROLES["agent_creator"]
    # Edit, not just Write: registration is three surgical edits to fail-loud
    # files, and overwriting those wholesale is how Toyota stops starting.
    assert {"Bash", "Read", "Write", "Edit"} <= set(role.allowed_tools)
    assert role.max_turns >= 60, "one run reads four documents and writes seven files"


async def test_it_is_told_to_use_absolute_paths_because_cwd_is_not_its_workspace():
    """routing_model.py runs every role with cwd=/home/adamsl/rol_finances, and
    AgentRole has no cwd field to override it (finance_team/roles.py is locked).
    Absolute paths in the prompt are the entire mitigation."""
    prompt = ROLES["agent_creator"].system_prompt
    assert "absolute path" in prompt.lower()
    assert "/home/adamsl/rol_finances" in prompt
    assert "/home/adamsl/agent_blocks/adws/" in prompt


async def test_it_must_verify_before_claiming_success_and_revert_on_failure():
    role = ROLES["agent_creator"]
    both = role.system_prompt + role.task_prompt
    assert "revert" in both.lower()
    # The gate itself, named as commands it has to run.
    assert "receptionist_team.agents.cli validate" in both
    assert "test_agent_catalog" in both


async def test_it_imports_the_interface_vocabulary_rather_than_restating_it():
    """An ADW that pastes its own copy of IPhase defeats sssf_roles.py's
    whole purpose; the prompt has to say so explicitly."""
    prompt = ROLES["agent_creator"].task_prompt
    assert "from sssf_roles import" in prompt
    assert "do not restate" in prompt.lower()


async def test_it_is_told_not_to_touch_the_locked_and_protected_files():
    prompt = ROLES["agent_creator"].system_prompt
    assert "finance_team/roles.py" in prompt
    assert "adw_modules" in prompt


async def test_sydney_knows_to_route_a_new_agent_request_here():
    assert "ROUTE: agent_creator" in SYDNEY_SYSTEM_PROMPT
    assert "agent_creator" in ROLE_BLURBS
    # Building a new teammate is not the mechanic changing an existing one.
    assert "toyota_mechanic" in SYDNEY_SYSTEM_PROMPT
    # And it is no longer Toyota's to do.
    assert not TOYOTA_ROSTER.has("agent_creator")
    # The user hears the spoken name, never the technical one.
    assert "agent_creator" not in delegation_acknowledgment("agent_creator")
    assert "our agent creator" in delegation_acknowledgment("agent_creator")


class _FakeRouteOnceModel:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, prompt: str) -> AgentReply:
        return AgentReply(text=self._text)


async def test_routing_model_resolves_the_agent_creator_by_name():
    """Without the extra_roles.py registration, ROUTE: agent_creator would hit
    RoutingModel's "I don't have an agent by that name" fallback forever."""
    calls = {}

    async def fake_run_role(role, cwd, model, echo=True):
        calls["role_name"] = role.name
        calls["task_prompt"] = role.task_prompt

        class _Result:
            is_error = False
            final_text = "built browser_opener and adw_open_browser.py; gate passed"
            cost_usd = 0.0

        return _Result()

    # run_role moved out of routing_model.py into the ToolAgentRunner --
    # RoutingModel now only matches the control line and hands the role to
    # an injected IDelegationRunner. The SDK call is still what's faked.
    original = tool_agent_runner_module.run_role
    tool_agent_runner_module.run_role = fake_run_role
    try:
        model = routing_model_module.RoutingModel(
            _FakeRouteOnceModel("ROUTE: agent_creator | create an agent that opens a browser"),
            model="fake-role-model",
            roster=SYDNEY_ROSTER,
            runner=tool_agent_runner_module.ToolAgentRunner(),
        )
        reply = await model.complete("I need an agent that can open a browser")
    finally:
        tool_agent_runner_module.run_role = original

    assert calls.get("role_name") == "agent_creator"
    # The user's words have to survive the relay, or the new agent gets built
    # for a purpose nobody asked for.
    assert "opens a browser" in calls.get("task_prompt", "")
    assert "don't have an agent" not in reply.text
    assert "gate passed" in reply.text


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

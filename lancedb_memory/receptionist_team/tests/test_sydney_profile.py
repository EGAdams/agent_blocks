"""Sydney: a clone of Toyota's engine, holding everything that isn't finance.

What has to hold for the clone to be a clone: the same chat model, recall
budget, runner, handoff decorator and shared LanceDB table as Toyota. What has
to hold for the split to be a split: every routable role belongs to exactly
one of them, a ROUTE line naming the other's specialist is refused rather
than run, and each points the user at the other with OPEN_AGENT.

Offline. The recall test uses a real LanceDB table in a tmpdir with the hash
embedder, same as test_shared_memory_wiring.py; nothing else touches disk.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_sydney_profile

Style note: this package does not follow the workspace bracket-spacing
convention (see receptionist_team/CLAUDE.md), so neither do these tests.
"""
import shutil
import tempfile
from pathlib import Path

from agent_memory.clock import SystemClock
from agent_memory.config import Settings
from agent_memory.models import AgentReply, EmbeddedTurn, Turn
from tests.fakes import ScriptedModel

from ..adapters.handoff_model import AgentHandoffModel
from ..adapters.routing_model import RoutingModel
from ..main import build_recall, build_shared_memory
from ..profiles import PROFILES, SYDNEY, TOYOTA, profile as named_profile
from ..roles import ROLES, TOYOTA_ROLES, TOYOTA_ROSTER
from ..sydney_roles import SYDNEY_ROLES, SYDNEY_ROSTER, SYDNEY_SYSTEM_PROMPT


class _RecordingRunner:
    def __init__(self):
        self.calls = []

    async def run(self, role, agent_name, model, extra=""):
        self.calls.append(agent_name)
        return AgentReply(text=f"ran {agent_name}", cost_usd=0.0)


def _routing(roster, reply):
    runner = _RecordingRunner()
    model = RoutingModel(
        ScriptedModel([reply]), model="fake-role-model", roster=roster, runner=runner,
    )
    return model, runner


async def test_sydney_is_a_profile_reached_with_agent_sydney():
    assert named_profile("sydney") is SYDNEY
    assert "sydney" in PROFILES
    assert SYDNEY.display_name == "Sydney"
    assert SYDNEY.default_session == "sydney" and SYDNEY.nexus_subdir == "sydney"
    assert SYDNEY.system_prompt == SYDNEY_SYSTEM_PROMPT


async def test_she_runs_on_toyotas_mechanics():
    # Same model, same recall budget, same shared memory, same hand-off
    # decorator, and the same Claude SDK runner Toyota's agent folders use.
    assert SYDNEY.chat_model == TOYOTA.chat_model
    assert SYDNEY.recall_max_chars == TOYOTA.recall_max_chars
    assert SYDNEY.peer_session == TOYOTA.name
    assert SYDNEY.handoff is True
    # Her agent folders still run on that runner; the post office is the one
    # specialist that isn't an SDK turn (adapters/post_office_runner.py).
    from ..adapters.post_office_runner import PostOfficeRunner
    from ..adapters.tool_agent_runner import ToolAgentRunner
    from ..delegation import RunnerMap
    runner = SYDNEY.build_runner()
    assert isinstance(runner, RunnerMap) and isinstance(runner._default, ToolAgentRunner)
    assert isinstance(runner._runners["sydney_post_office"], PostOfficeRunner)
    # Not Toyota's finance-only WORK stack or her forced finance routes; her
    # one forced route sends work for Sonnet or Opus to the post office.
    from ..adapters.post_office_route import PostOfficeRoute
    assert SYDNEY.operations is False
    assert isinstance(SYDNEY.route_override, PostOfficeRoute)
    # Her own port and voice, so two sidecars and two voices can run at once.
    assert SYDNEY.service_port == 8769
    assert SYDNEY.tts_voice and SYDNEY.tts_voice != TOYOTA.tts_voice


async def test_both_rosters_come_from_the_one_role_assignment():
    from ..role_desks import NamedRolesOrDefault, roles_for
    from ..roles import ROLE_ASSIGNMENT
    assert TOYOTA_ROLES == roles_for("toyota", ROLES, ROLE_ASSIGNMENT)
    assert SYDNEY_ROLES == roles_for("sydney", ROLES, ROLE_ASSIGNMENT)
    # An agent nobody named lands on Sydney's desk: creating agents is her job.
    assert ROLE_ASSIGNMENT.owner_of("a_brand_new_agent") == "sydney"
    # The rule itself, apart from the real roster.
    rule = NamedRolesOrDefault(["a"], named_desk="x", default_desk="y")
    assert roles_for("x", {"a": 1, "b": 2}, rule) == {"a": 1}
    assert roles_for("y", {"a": 1, "b": 2}, rule) == {"b": 2}


async def test_every_role_belongs_to_exactly_one_of_them():
    assert set(SYDNEY_ROLES) | set(TOYOTA_ROLES) == set(ROLES)
    assert not set(SYDNEY_ROLES) & set(TOYOTA_ROLES)
    for name in ("toyota_mechanic", "mechanic_translator", "agent_creator",
                 "browser_opener", "search_link_opener", "dashboard_expert",
                 "web_search_expert", "memory_compactor", "adw_guide",
                 "ask_opus", "ask_sonnet"):
        assert SYDNEY_ROSTER.has(name), name
        assert not TOYOTA_ROSTER.has(name), name
    for name in ("live_books", "category_spreadsheet", "spreadsheet_reshaper",
                 "tax_progress_page", "progress_check", "synthesizer"):
        assert TOYOTA_ROSTER.has(name), name
        assert not SYDNEY_ROSTER.has(name), name
    # The mechanic dispatcher is routable; its coder never is.
    assert "mechanic_coder" not in SYDNEY_ROLES


async def test_sydney_runs_the_mechanic_and_toyota_refuses_to():
    model, runner = _routing(SYDNEY_ROSTER, "ROUTE: toyota_mechanic | diagnose only: Toyota answers slowly")
    reply = await model.complete("USER: Toyota is slow, ask the mechanic\nAGENT:")
    assert runner.calls == ["toyota_mechanic"], runner.calls
    assert "ran toyota_mechanic" in reply.text

    model, runner = _routing(TOYOTA_ROSTER, "ROUTE: toyota_mechanic | fix yourself")
    reply = await model.complete("USER: fix yourself\nAGENT:")
    assert runner.calls == [], runner.calls
    assert "don't have an agent" in reply.text


async def test_sydney_refuses_a_finance_route():
    model, runner = _routing(SYDNEY_ROSTER, "ROUTE: live_books | how much did we spend in March")
    reply = await model.complete("USER: how much did we spend in March\nAGENT:")
    assert runner.calls == [], runner.calls
    assert "don't have an agent" in reply.text


async def test_each_hands_the_other_off_with_open_agent():
    assert "OPEN_AGENT: toyota" in SYDNEY_SYSTEM_PROMPT
    assert "OPEN_AGENT: sydney" in TOYOTA.system_prompt
    for key, name in (("toyota", "Toyota"), ("sydney", "Sydney")):
        seen = []
        model = AgentHandoffModel(ScriptedModel([f"OPEN_AGENT: {key}"]), on_handoff=seen.append)
        reply = await model.complete("p")
        assert seen == [{"agent": key, "name": name}], seen
        assert name in reply.text


async def test_toyota_and_sydney_recall_each_other_through_one_table():
    workdir = Path(tempfile.mkdtemp(prefix="sydney_profile_"))
    try:
        settings = Settings(
            session_id="toyota", embedding_profile="hash",
            lancedb_uri=workdir / "lance_data", table_name="sydney_profile_test",
        )
        embedder, store = build_shared_memory(settings)
        clock = SystemClock()

        async def said(session, message):
            turn = Turn(session_id=session, speaker="USER", message=message, created_at=clock.now())
            store.add([EmbeddedTurn(turn=turn, vector=await embedder.embed(message))])

        await said("sydney", "the deploy branch is release-seven")
        await said("toyota", "the utilities budget is tight this year")
        await said("mazda", "statement for March checked")

        query = await embedder.embed("what is the deploy branch")
        toyota_heard = [t.message for t in build_recall(TOYOTA, settings, store).recall("toyota", query)]
        assert any(m.startswith("[with Sydney]") and "release-seven" in m for m in toyota_heard), toyota_heard
        assert any(m.startswith("[with Mazda]") for m in toyota_heard), toyota_heard

        query = await embedder.embed("utilities budget")
        sydney_heard = [t.message for t in build_recall(SYDNEY, settings, store).recall("sydney", query)]
        assert any(m.startswith("[with Toyota]") and "utilities" in m for m in sydney_heard), sydney_heard
        # Her own turn is hers, unlabelled.
        assert any(m == "the deploy branch is release-seven" for m in sydney_heard), sydney_heard
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

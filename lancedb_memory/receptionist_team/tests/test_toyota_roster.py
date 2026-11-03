"""Toyota's explicit roster (spec Phase 4, items 1-3 and 6).

Offline: run_role is faked, no model runs, nothing is created or repaired --
agent_creator and toyota_mechanic are dispatched to a recording double only.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_toyota_roster
"""
import os
import re
from dataclasses import replace
from types import SimpleNamespace

from pydantic import ValidationError

from finance_team.roles import ROLES as FINANCE_ROLES
from tests.fakes import ScriptedModel

from ..adapters import tool_agent_runner as tool_agent_runner_module
from ..adapters.routing_model import RoutingModel
from ..operations.workflow_model.capabilities import CAPABILITIES
from ..profiles import TOYOTA
from ..roles import ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, ROLES, TOYOTA_MEMBERSHIP, TOYOTA_ROSTER, TOYOTA_SYSTEM_PROMPT
from ..roster_membership import RosterMembership, RosterMembershipError
from ..rosters import Roster
from ..sydney_roles import SYDNEY_ROSTER

# The finance routes Toyota had before the explicit roster. They stay enabled.
FINANCE_ROUTES = {"mapper_cash", "mapper_card", "mapper_bank", "mapper_other", "synthesizer",
                  "receipt_scanner", "expense_reconciler", "progress_check"}
KEPT = {"category_spreadsheet", "spreadsheet_reshaper", "live_books", "gifts_love_offerings_report",
        "tax_progress_page", "company_todos", "scala_questions", "toyota_post_office"}


def _routing(reply, runner, roster=TOYOTA_ROSTER, acks=None):
    return RoutingModel(ScriptedModel([reply]), model="fake-role-model", roster=roster, runner=runner,
                        speaker="Toyota", on_delegation=(acks.append if acks is not None else None))


class Recorder:
    def __init__(self):
        self.calls = []

    async def run(self, role, agent_name, model, extra=""):
        from agent_memory.models import AgentReply
        self.calls.append((agent_name, extra))
        return AgentReply(text=f"ran {agent_name}")


# -- item 1 ------------------------------------------------------------------
async def test_toyota_advertises_creator_and_mechanic_and_keeps_her_routes():
    names = set(TOYOTA_ROSTER.names())
    assert {"agent_creator", "toyota_mechanic", "star_trek_bridge"} <= names
    assert KEPT <= names, KEPT - names
    for name in ("agent_creator", "toyota_mechanic", "star_trek_bridge"):
        assert f"ROUTE: {name}" in TOYOTA_SYSTEM_PROMPT, name
        assert name in TOYOTA_ROSTER.allowed_agents()


async def test_creator_and_mechanic_dispatch_through_the_real_runner_map_to_a_mock():
    seen = []

    async def fake_run_role(role, cwd, model, echo=True):
        seen.append(role.name)
        return SimpleNamespace(is_error=False, final_text=f"{role.name} ok", cost_usd=None)

    original = tool_agent_runner_module.run_role
    tool_agent_runner_module.run_role = fake_run_role
    try:
        runner = TOYOTA.build_runner()
        for name in ("agent_creator", "toyota_mechanic"):
            acks = []
            reply = await _routing(f"ROUTE: {name} | diagnose only", runner, acks=acks).complete("USER: hi\nAGENT:")
            assert acks, f"no acknowledgment for {name}"
            assert name in reply.text, reply.text
    finally:
        tool_agent_runner_module.run_role = original
    assert seen == ["agent_creator", "toyota_mechanic"], seen


# -- item 2 ------------------------------------------------------------------
async def test_existing_finance_routes_stay_on_her_roster_and_route():
    recorder = Recorder()
    for name in sorted(FINANCE_ROUTES):
        assert name in TOYOTA_ROSTER.names() and not TOYOTA_MEMBERSHIP.is_disabled(name), name
        reply = await _routing(f"ROUTE: {name}", recorder).complete("USER: run it\nAGENT:")
        assert f"ran {name}" in reply.text, (name, reply.text)
    assert [c[0] for c in recorder.calls] == sorted(FINANCE_ROUTES)


async def test_every_finance_team_role_is_enabled_or_explicitly_disabled():
    unplaced = set(FINANCE_ROLES) - set(TOYOTA_MEMBERSHIP.enabled) - set(TOYOTA_MEMBERSHIP.disabled)
    assert not unplaced, f"new finance_team roles missing from toyota_roster.json: {sorted(unplaced)}"


async def test_no_prompt_or_capability_line_routes_to_a_name_off_her_roster():
    names = set(TOYOTA_ROSTER.names())
    for text in (TOYOTA_SYSTEM_PROMPT, CAPABILITIES):
        routed = set(re.findall(r"ROUTE: ([a-z_]+)", text))
        assert routed <= names, routed - names
        assert not routed & set(TOYOTA_MEMBERSHIP.disabled)


async def test_finance_team_and_sydney_rosters_and_helpers_are_intact():
    assert FINANCE_ROUTES <= set(FINANCE_ROLES) and FINANCE_ROUTES <= set(ROLES)
    for name in FINANCE_ROUTES:
        assert name in ROLE_BLURBS and name in ROLE_SPOKEN_NAMES and name in ROLE_START_TASKS
    assert set(SYDNEY_ROSTER.names()) == {
        "adw_guide", "agent_creator", "ask_opus", "ask_sonnet", "browser_opener", "dashboard_expert",
        "finance_report", "mechanic_translator", "memory_compactor", "search_link_opener",
        "sydney_post_office", "system_mechanic", "toyota_mechanic", "web_search_expert"}
    assert not FINANCE_ROUTES & set(SYDNEY_ROSTER.names())


async def test_the_runner_map_covers_every_deterministic_route_and_bridge():
    runners = TOYOTA.build_runner()._runners
    assert type(runners["star_trek_bridge"]).__name__ == "BridgeDelegationRunner"
    assert type(runners["toyota_post_office"]).__name__ == "PostOfficeRunner"
    assert set(runners) <= set(TOYOTA_ROSTER.names()), set(runners) - set(TOYOTA_ROSTER.names())


# -- item 3 ------------------------------------------------------------------
def _with_specialist(enabled, disabled=None):
    roles = dict(ROLES, test_specialist=replace(ROLES["live_books"], name="test_specialist"))
    membership = RosterMembership(owner="Toyota", enabled=enabled, disabled=disabled or {})
    selected = membership.select(roles)
    return Roster(owner="Toyota", roles=selected,
                  blurbs=dict(ROLE_BLURBS, test_specialist="answers test questions"),
                  spoken_names=dict(ROLE_SPOKEN_NAMES, test_specialist="the test specialist"),
                  start_tasks=dict(ROLE_START_TASKS, test_specialist="a test"))


async def test_a_configured_specialist_routes_once_it_is_defined_and_listed():
    roster = _with_specialist(("live_books", "test_specialist"))
    recorder = Recorder()
    reply = await _routing("ROUTE: test_specialist | hello", recorder, roster=roster).complete("USER: x\nAGENT:")
    assert recorder.calls == [("test_specialist", "hello")], recorder.calls
    assert "ran test_specialist" in reply.text


async def test_unknown_and_disabled_specialists_are_rejected():
    try:
        RosterMembership(owner="Toyota", enabled=("never_defined",)).select(ROLES)
        raise AssertionError("an undefined role was accepted")
    except RosterMembershipError as exc:
        assert "never_defined" in str(exc)
    for bad in ({"enabled": ("live_books",), "disabled": {"live_books": {"purpose": "dup"}}},
                {"enabled": ("live_books", "live_books")},
                {"enabled": (), "disabled": {"synthesizer": {}}},
                {"enabled": (), "surprise": True}):
        try:
            RosterMembership(owner="Toyota", **bad)
            raise AssertionError(f"accepted {bad}")
        except ValidationError:
            pass
    roster = _with_specialist(("live_books",), {"test_specialist": {"purpose": "a parked test"}})
    recorder = Recorder()
    await _routing("ROUTE: test_specialist", recorder, roster=roster).complete("USER: x\nAGENT:")
    assert recorder.calls == []


# -- item 6 ------------------------------------------------------------------
async def test_bridge_route_acknowledges_first_and_reports_unavailable_without_a_credential():
    saved = {k: os.environ.pop(k) for k in ("TOYOTA_BRIDGE_CLIENT_TOKEN", "TOYOTA_BRIDGE_CLIENT_TOKEN_FILE")
             if k in os.environ}
    try:
        order = []
        routing = _routing("ROUTE: star_trek_bridge | say hello world", TOYOTA.build_runner(), acks=order)
        reply = await routing.complete("USER: ask the bridge to say hello\nAGENT:")
        assert order and "Star Trek Bridge" in order[0], order
        assert "isn't available" in reply.text and "Nothing was sent" in reply.text, reply.text
    finally:
        os.environ.update(saved)


async def test_profile_carries_the_rendered_prompt_and_roster():
    assert TOYOTA.roster is TOYOTA_ROSTER and TOYOTA.system_prompt == TOYOTA_SYSTEM_PROMPT
    assert "star_trek_bridge" in TOYOTA_ROSTER.roster_text()


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

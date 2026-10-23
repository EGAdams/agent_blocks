"""The sidecar serves whichever receptionist --agent names.

service.py used to be Toyota's alone. These pin what had to become
profile-driven for Mazda to get a browser window of her own: the port, the
session and nexus subdirectory, the /health identity, and the fact that only
Toyota hosts a guest's shared memory.

Offline: a fake agent, no torch, no LanceDB, no Groq.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_service_profiles

Style note: this package does not follow the workspace bracket-spacing
convention (see receptionist_team/CLAUDE.md), so neither do these tests.
"""
from pathlib import Path

from agent_memory.config import Settings
from agent_memory.models import AgentReply

from .. import service
from ..profiles import MAZDA, PROFILES, TOYOTA
from ..service_models import MemoryComposeRequest, TurnRequest
from ..turn_runner import _DelegationRelay, _TurnRunner


class _FakeCost:
    total_cost_usd = 0.0


class _StillAgent:
    async def handle_turn(self, text, request_id=None):
        return AgentReply(text="ok", cost_usd=0.0)


def _runner(profile, desk=None):
    settings = Settings(
        session_id=profile.default_session, model="fake", embedding_profile="hash"
    )
    return _TurnRunner(
        _StillAgent(), _FakeCost(), settings, "fake-role-model", _DelegationRelay(),
        memory_desk=desk, profile=profile,
    )


async def test_each_receptionist_has_her_own_loopback_port():
    # Two sidecars run side by side, so a shared port would mean the dashboard
    # talking to whichever one won the bind. profiles.py asserts this at
    # import; this is the test that says why it matters.
    ports = sorted(p.service_port for p in PROFILES.values())
    assert ports == [8767, 8768], ports
    assert len(set(ports)) == len(ports)
    # 8765 is the dashboard itself and 8766 is thought_bridge.py on this box.
    assert 8765 not in ports and 8766 not in ports


async def test_health_names_which_receptionist_answered():
    # The dashboard probes /health to decide whether a box has a warm sidecar
    # behind it. With two of them, "ok" is no longer enough to identify one.
    assert _runner(MAZDA).health()["agent"] == "mazda"
    assert _runner(MAZDA).health()["display_name"] == "Mazda"
    assert _runner(TOYOTA).health()["agent"] == "toyota"


async def test_a_turn_reports_the_profiles_own_session():
    records = list(_runner(MAZDA).stream(TurnRequest(text="hello")))

    assert [r.kind for r in records] == ["reply", "result"], [r.kind for r in records]
    assert records[-1].session == "mazda"


async def test_only_toyota_hosts_the_letta_guests_memory():
    # Toyota answers /memory/compose on behalf of the dashboard's Letta Mazda,
    # whose turns run outside this package. Mazda's own sidecar has no guest,
    # and must say so rather than pretend to recall nothing.
    assert TOYOTA.serves_peer_memory is True
    assert MAZDA.serves_peer_memory is False

    answer = _runner(MAZDA).compose_memory(MemoryComposeRequest(session="mazda", text="hi"))
    assert answer["ok"] is False
    assert "shared memory" in answer["error"]
    assert _runner(MAZDA).health()["shared_memory"] is False


async def test_the_sidecar_still_defaults_to_toyota():
    # The unit file, the dashboard bridge and every older invocation all say
    # `-m receptionist_team.service` with no --agent.
    assert service.DEFAULT_PORT == TOYOTA.service_port
    assert _TurnRunner.build.__defaults__ == (TOYOTA,)


async def test_each_receptionist_carries_her_own_chat_model():
    # Both are on the 20b: the 120b spent the whole 200k-token daily Groq
    # allowance in roughly forty turns, Mazda first and then Toyota. They
    # happen to agree today, but the model stays a profile field rather than a
    # shared CLI default, so either can move back up without moving the other.
    assert TOYOTA.chat_model == "openai/gpt-oss-120b"
    assert MAZDA.chat_model == "openai/gpt-oss-120b"
    # Both entrypoints must fall back to the profile, or it is quietly ignored.
    here = Path(__file__).resolve().parent.parent
    for entrypoint in ("main.py", "service.py"):
        source = (here / entrypoint).read_text()
        assert "model=args.model or profile.chat_model" in source, entrypoint


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

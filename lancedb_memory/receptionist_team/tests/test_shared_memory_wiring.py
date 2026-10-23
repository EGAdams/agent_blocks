"""Integration test for receptionist_team's own LanceDB wiring.

Unlike test_service_turns.py (which drives the sidecar with a _FakeDesk so
it never loads LanceDB), this exercises the real composition functions in
main.py -- build_shared_memory() and build_mazda_desk() -- against a real
LanceDBMemoryStore on disk. The "hash" embedding profile keeps it free and
instant (no torch, no network), same trick agent_memory/tests/test_lancedb_store.py
uses, but here it is receptionist_team's own build_* functions under test,
not agent_memory's ConversationAgent directly -- so a break in how Toyota and
Mazda share one table (session scoping, peer-session recall) would fail here
even if agent_memory's own tests stay green.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_shared_memory_wiring
"""
import shutil
import tempfile
from pathlib import Path

from agent_memory.config import Settings

from ..main import build_mazda_desk, build_shared_memory

TOYOTA_SESSION = "toyota-shared-test"
MAZDA_SESSION = "mazda"


def _settings(workdir: Path) -> Settings:
    return Settings(
        session_id=TOYOTA_SESSION,
        embedding_profile="hash",
        lancedb_uri=workdir / "lance_data",
        table_name="shared_memory_wiring_test",
    )


async def test_mazda_desk_persists_and_recalls_through_a_real_lancedb_table():
    workdir = Path(tempfile.mkdtemp(prefix="shared_memory_wiring_"))
    try:
        embedder, store = build_shared_memory(_settings(workdir))
        desk = build_mazda_desk(embedder, store, toyota_session=lambda: TOYOTA_SESSION)

        await desk.record(MAZDA_SESSION, "my favourite colour is teal", "Noted, teal.")
        prompt, recalled = await desk.compose(MAZDA_SESSION, "what's my favourite colour?")

        assert recalled >= 1, recalled
        assert "teal" in prompt, prompt
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_mazda_desk_recalls_toyota_s_session_too():
    """build_mazda_desk wires PeerSessionRecall(peer_session=toyota_session) --
    what the user told Toyota must be visible to Mazda's recall, through the
    same real table, without Mazda ever writing it herself."""
    workdir = Path(tempfile.mkdtemp(prefix="shared_memory_wiring_"))
    try:
        embedder, store = build_shared_memory(_settings(workdir))
        desk = build_mazda_desk(embedder, store, toyota_session=lambda: TOYOTA_SESSION)

        # Simulate a Toyota turn landing directly in the shared store (as
        # agent_memory.agent.ConversationAgent.handle_turn would after a real
        # conversation), with no Mazda write at all.
        from agent_memory.models import EmbeddedTurn, Turn
        from agent_memory.clock import SystemClock

        clock = SystemClock()
        message = "I live at 42 Wallaby Way"
        turn = Turn(session_id=TOYOTA_SESSION, speaker="USER", message=message, created_at=clock.now())
        store.add([EmbeddedTurn(turn=turn, vector=await embedder.embed(message))])

        prompt, recalled = await desk.compose(MAZDA_SESSION, "what's my address?")

        assert recalled >= 1, recalled
        assert "Wallaby Way" in prompt, prompt
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

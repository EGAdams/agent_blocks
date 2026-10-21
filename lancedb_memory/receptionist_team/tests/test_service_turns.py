"""Offline tests for the Toyota sidecar's turn loop.

The first tests in this package. They cover service.py's own job -- turning
one handle_turn call into an ordered stream of records -- with a fake agent,
so nothing here loads torch, opens a LanceDB table, calls Groq, or touches
the finance database.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_service_turns

Style note: this package does not follow the workspace bracket-spacing
convention (see receptionist_team/CLAUDE.md), so neither do these tests --
unlike lancedb_memory/tests/, which does.
"""
import asyncio

from pydantic import ValidationError

from agent_memory.config import Settings
from agent_memory.models import AgentReply

from ..service import _DelegationRelay, _TurnRunner
from ..service_models import (
    MAX_TURN_CHARS, MemoryComposeRequest, MemoryRecordRequest, TurnRequest,
)

ACK = "Please wait a few seconds while I contact our receipt scanner."


class _FakeCost:
    total_cost_usd = 0.25


class _ScriptedAgent:
    """Stands in for ConversationAgent: one reply, optional ack, optional boom."""

    def __init__(self, reply="All set.", relay=None, ack=None, fail=None, gate=None,
                 handoff=None):
        self._reply = reply
        self._handoff = handoff
        self._relay = relay
        self._ack = ack
        self._fail = fail
        self._gate = gate
        self.seen = []
        self.cleared = 0

    def clear_context(self):
        self.cleared += 1
        return f"toyota-test--ctx{self.cleared}"

    async def handle_turn(self, text, request_id=None):
        self.seen.append(text)
        if self._ack is not None and self._relay is not None:
            self._relay(self._ack)
        if self._handoff is not None:
            self._handoff({"agent": "mazda", "name": "Mazda"})
        if self._gate is not None:
            await self._gate.wait()
        await asyncio.sleep(0)
        if self._fail is not None:
            raise self._fail
        return AgentReply(text=self._reply, cost_usd=0.01)


class _FakeDesk:
    def __init__(self):
        self.recorded = []

    async def compose(self, session_id, user_message):
        return f"[memory for {session_id}]\n{user_message}", 1

    async def record(self, session_id, user_message, agent_message):
        self.recorded.append((session_id, user_message, agent_message))


def _runner(agent, relay=None, handoff_relay=None, desk=None):
    settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
    return _TurnRunner(
        agent, _FakeCost(), settings, "fake-role-model", relay or _DelegationRelay(),
        handoff_relay=handoff_relay, memory_desk=desk,
    )


def _drain(runner, text):
    return list(runner.stream(TurnRequest(text=text)))


async def test_plain_turn_yields_reply_then_result():
    runner = _runner(_ScriptedAgent(reply="Hi there."))
    records = _drain(runner, "hello")

    assert [r.kind for r in records] == ["reply", "result"], [r.kind for r in records]
    assert records[0].text == "Hi there."
    assert records[1].session == "toyota-test", records[1].session
    assert records[-1].is_terminal


async def test_acknowledgment_arrives_before_the_reply():
    """The whole point of streaming: the browser can speak the ack while the
    delegated finance role is still running."""
    relay = _DelegationRelay()
    agent = _ScriptedAgent(reply="Here is what it found.", relay=relay, ack=ACK)
    records = _drain(_runner(agent, relay), "scan my receipts")

    assert [r.kind for r in records] == ["acknowledgment", "reply", "result"]
    assert records[0].text == ACK


async def test_failed_turn_becomes_one_error_record():
    runner = _runner(_ScriptedAgent(fail=RuntimeError("groq exploded")))
    records = _drain(runner, "hello")

    assert [r.kind for r in records] == ["error"], [r.kind for r in records]
    assert "groq exploded" in records[0].text
    assert records[0].is_terminal


async def test_relay_is_unbound_after_a_turn():
    """A late ack from a finished turn must not leak into the next one's stream."""
    relay = _DelegationRelay()
    agent = _ScriptedAgent(relay=relay, ack=ACK)
    runner = _runner(agent, relay)
    _drain(runner, "first")

    relay("a straggler nobody is listening for")  # must not raise
    records = _drain(runner, "second")
    assert [r.kind for r in records] == ["acknowledgment", "reply", "result"]


async def test_turns_serialize_so_memory_stays_ordered():
    """recall-before-persist only holds if two turns can't interleave."""
    gate = asyncio.Event()
    agent = _ScriptedAgent(gate=gate)
    runner = _runner(agent)

    first = asyncio.get_running_loop().run_in_executor(None, _drain, runner, "first")
    second = asyncio.get_running_loop().run_in_executor(None, _drain, runner, "second")
    await asyncio.sleep(0.05)
    assert agent.seen == ["first"], f"second turn started early: {agent.seen}"

    runner._loop.call_soon_threadsafe(gate.set)
    await first
    await second
    assert agent.seen == ["first", "second"], agent.seen


async def test_clear_context_rotates_session_and_calls_the_agent():
    """The dashboard's 'compact conversation' button maps to this: the warm
    agent is told to forget its context, and the next turn's result reports
    the new session id -- /health and later TurnRecords must agree."""
    agent = _ScriptedAgent(reply="Fresh start.")
    runner = _runner(agent)

    before = _drain(runner, "hello")
    assert before[-1].session == "toyota-test"

    result = runner.clear_context()
    assert result == {"ok": True, "session": "toyota-test--ctx1"}
    assert agent.cleared == 1
    assert runner.health()["session"] == "toyota-test--ctx1"

    after = _drain(runner, "hello again")
    assert after[-1].session == "toyota-test--ctx1"


async def test_handoff_arrives_before_the_reply():
    """The browser opens Mazda's window while Toyota's sentence is spoken."""
    handoff = _DelegationRelay()
    agent = _ScriptedAgent(reply="Opening Mazda.", handoff=handoff)
    records = _drain(_runner(agent, handoff_relay=handoff), "open Mazda")

    assert [r.kind for r in records] == ["handoff", "reply", "result"], [r.kind for r in records]
    assert records[0].handoff == {"agent": "mazda", "name": "Mazda"}


async def test_memory_compose_and_record_go_through_the_desk():
    desk = _FakeDesk()
    runner = _runner(_ScriptedAgent(), desk=desk)

    composed = runner.compose_memory(MemoryComposeRequest(session="mazda", text="hi"))
    assert composed == {"ok": True, "prompt": "[memory for mazda]\nhi", "recalled": 1}, composed
    assert runner.record_memory(MemoryRecordRequest(session="mazda", user="hi", agent="hello")) == {"ok": True}
    assert desk.recorded == [("mazda", "hi", "hello")], desk.recorded
    assert runner.health()["shared_memory"] is True


async def test_memory_without_a_desk_fails_soft():
    runner = _runner(_ScriptedAgent())
    result = runner.compose_memory(MemoryComposeRequest(session="mazda", text="hi"))
    assert result["ok"] is False, result


async def test_request_rejects_empty_and_oversized_text():
    for bad in ("", "   ", "\n\n"):
        try:
            TurnRequest(text=bad)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"accepted blank text: {bad!r}")

    try:
        TurnRequest(text="x" * (MAX_TURN_CHARS + 1))
    except ValidationError:
        pass
    else:
        raise AssertionError("accepted oversized text")


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

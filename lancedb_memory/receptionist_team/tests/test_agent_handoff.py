"""Offline tests for AgentHandoffModel (Toyota opening Mazda's window).

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_agent_handoff
"""
from tests.fakes import ScriptedModel

from ..adapters.handoff_model import AgentHandoffModel


def _model(reply, sink=None):
    return AgentHandoffModel(ScriptedModel([reply]), on_handoff=sink)


async def test_directive_emits_handoff_and_speaks_the_sentence():
    seen = []
    reply = await _model("OPEN_AGENT: mazda | Bringing Mazda up for you.", seen.append).complete("p")
    assert seen == [{"agent": "mazda", "name": "Mazda"}], seen
    assert reply.text == "Bringing Mazda up for you."


async def test_directive_without_sentence_uses_a_default():
    seen = []
    reply = await _model("  OPEN_AGENT: Mazda  ", seen.append).complete("p")
    assert seen == [{"agent": "mazda", "name": "Mazda"}], seen
    assert "Mazda" in reply.text and "OPEN_AGENT" not in reply.text, reply.text


async def test_plain_reply_passes_through_untouched():
    seen = []
    text = "You can say OPEN_AGENT: mazda to me any time."
    reply = await _model(text, seen.append).complete("p")
    assert seen == [] and reply.text == text, (seen, reply.text)


async def test_unknown_agent_is_not_handed_off():
    seen = []
    reply = await _model("OPEN_AGENT: honda", seen.append).complete("p")
    assert seen == [], seen
    assert "Mazda" in reply.text, reply.text


async def test_no_sink_means_no_window():
    reply = await _model("OPEN_AGENT: mazda").complete("p")
    assert "dashboard" in reply.text, reply.text


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

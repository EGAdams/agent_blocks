"""Liz: Toyota's engine, her own city-tax roster, and a route the model doesn't get to choose."""
from agent_memory.models import AgentReply
from tests.fakes import ScriptedModel
from ..profiles import PROFILES, LIZ, TOYOTA
from ..adapters.routing_model import RoutingModel
from ..adapters.city_tax_runner import LizDeskRunner


def prompt(text):
    return f"USER: what are we missing?\nAGENT: old answer\nUSER: {text}\nAGENT:"


async def test_liz_is_the_shared_engine_with_her_own_city_tax_roster():
    assert PROFILES["liz"] is LIZ
    assert LIZ.chat_model == TOYOTA.chat_model and LIZ.service_port == 8772
    assert LIZ.default_session == "liz" and LIZ.nexus_subdir == "liz"
    assert set(LIZ.roster.names()) == {"city_tax_desk"}
    assert isinstance(LIZ.build_runner(), LizDeskRunner)
    assert LIZ.peer_session is None and not LIZ.operations
    assert "Never file the return" in LIZ.system_prompt and "ROUTE: city_tax_desk" in LIZ.system_prompt


async def test_city_tax_sentences_reach_the_desk_and_a_greeting_reaches_the_model():
    class Runner:
        calls = []
        async def run(self, role, name, model, extra):
            self.calls.append((name, extra)); return AgentReply(text="desk line")
    for text in ["Liz, what changed between the 2024 and 2025 forms?", "What information are we missing?",
                 "I've finished scanning it.", "Resume the tax preparation.", "Show me the completed city tax form."]:
        runner = Runner(); runner.calls = []
        model = RoutingModel(ScriptedModel([]), "unused", LIZ.roster, runner, override=LIZ.route_override)
        assert (await model.complete(prompt(text))).text == "desk line"
        assert runner.calls == [("city_tax_desk", text)]
    assert LIZ.route_override.forced_agent(prompt("Good morning Liz")) is None
    # a recalled "what are we missing?" never forces the route by itself
    assert LIZ.route_override.forced_agent("USER: what are we missing?\nAGENT: x\nUSER: thanks\nAGENT:") is None


async def test_the_desk_runner_passes_the_whole_sentence_and_repeats_the_line():
    class Workflow:
        seen = None
        def line(self, purpose, flags):
            Workflow.seen = flags; return "the ADW's line"
    reply = await LizDeskRunner(Workflow()).run(None, "city_tax_desk", "m", "What are we missing?")
    assert reply.text == "the ADW's line" and Workflow.seen == ["--request", "What are we missing?"]


if __name__ == "__main__":
    from tests.runner import main
    main(dict(globals()))

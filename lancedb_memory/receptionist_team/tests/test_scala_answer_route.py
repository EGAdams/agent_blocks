"""Toyota's route to Scala's filing questions: answered by the ADW, never by the model.

Offline -- the runner's workflow is faked, so no subprocess, no Groq, no SDK.
"""
from agent_memory.models import AgentReply

from ..adapters.routing_model import RoutingModel
from ..adapters.scala_answer_runner import ScalaAnswerRunner
from ..adapters.scala_route import ScalaAnswerRoute
from ..delegation import RunnerMap
from ..profiles import TOYOTA
from ..roles import ROLES, TOYOTA_ROSTER

ROUTE = ScalaAnswerRoute()


def _prompt(message, recalled=""):
    return f"{recalled}\n\nUSER: {message}\nAGENT:"


class _CountingModel:
    def __init__(self):
        self.calls = 0

    async def complete(self, prompt):
        self.calls += 1
        return AgentReply(text="Done, Scala filed it.")


class _FakeWorkflow:
    def __init__(self):
        self.calls = []

    def line(self, purpose, flags):
        self.calls.append(flags)
        return "Scala filed question #7 under bills_511/water (new folder) and checked it off the to-do list."


async def test_answers_to_scala_are_forced_to_her():
    for message in ("Scala, file #7 under documents_511/insurance", "what is scala waiting on?",
                    "put to-do 7 in bills_511/water"):
        assert ROUTE.forced_agent(_prompt(message)) == "scala_questions", message


async def test_other_talk_is_left_alone():
    for message in ("what's on the to-do list?", "how much did we spend in 2025", "hello Toyota"):
        assert ROUTE.forced_agent(_prompt(message)) is None, message
    assert ROUTE.forced_agent(_prompt("thanks", recalled="USER: Scala, file #7 under bills_511/water")) is None


async def test_the_forced_route_skips_the_model_and_repeats_the_adw_line():
    inner, workflow = _CountingModel(), _FakeWorkflow()
    runner = ScalaAnswerRunner(workflow=workflow)
    model = RoutingModel(inner, model="unused", roster=TOYOTA_ROSTER,
                         runner=RunnerMap({"scala_questions": runner}, default=runner),
                         speaker="Toyota", override=ROUTE)

    reply = await model.complete(_prompt("Scala, file #7 under bills_511/water"))

    assert inner.calls == 0, "a model that is asked gets to say it was filed"
    assert workflow.calls == [["--request", "Scala, file #7 under bills_511/water"]]
    assert "bills_511/water" in reply.text


async def test_toyota_asks_scala_before_the_to_do_list():
    assert TOYOTA.route_override.forced_agent(
        _prompt("file to-do #7 under bills_511/water")) == "scala_questions"
    assert TOYOTA.route_override.forced_agent(_prompt("what's on the to-do list?")) == "company_todos"
    assert "scala_questions" in ROLES and "scala_questions" in TOYOTA_ROSTER.names()


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

"""Toyota's company to-do route: the list is read and changed by its ADW, never recalled.

Offline -- the runner's workflow is faked, so no subprocess, no Groq, no SDK.
"""
from agent_memory.models import AgentReply

from ..adapters.company_todo_runner import CompanyTodoRunner
from ..adapters.routing_model import RoutingModel
from ..adapters.todo_route import CompanyTodoRoute
from ..delegation import RunnerMap
from ..profiles import TOYOTA
from ..roles import ROLES, TOYOTA_ROSTER

ROUTE = CompanyTodoRoute()


def _prompt(message, recalled=""):
    return f"{recalled}\n\nUSER: {message}\nAGENT:"


class _CountingModel:
    def __init__(self):
        self.calls = 0

    async def complete(self, prompt):
        self.calls += 1
        return AgentReply(text="Sure, the list has three things on it.")


class _FakeWorkflow:
    def __init__(self):
        self.calls = []

    def line(self, purpose, flags):
        self.calls.append(flags)
        return "Added to the company to-do list as #1 order toner. 1 open now."


async def test_to_do_requests_are_forced_to_the_list():
    for message in ("add order toner to the to-do list", "what's on the todo list?",
                    "mark number 2 done on the to-do list", "to-do: call the roofer"):
        assert ROUTE.forced_agent(_prompt(message)) == "company_todos", message


async def test_talk_about_wanting_a_list_and_other_talk_is_left_to_the_model():
    for message in ("We need a company todo list man", "how much did we spend in 2025", "hello Toyota"):
        assert ROUTE.forced_agent(_prompt(message)) is None, message


async def test_a_recalled_to_do_mention_does_not_force_the_route():
    assert ROUTE.forced_agent(_prompt("thanks", recalled="USER: add toner to the to-do list")) is None


async def test_the_forced_route_skips_the_model_and_repeats_the_adw_line():
    inner, workflow = _CountingModel(), _FakeWorkflow()
    runner = CompanyTodoRunner(workflow=workflow)
    model = RoutingModel(inner, model="unused", roster=TOYOTA_ROSTER,
                         runner=RunnerMap({"company_todos": runner}, default=runner),
                         speaker="Toyota", override=ROUTE)

    reply = await model.complete(_prompt("add order toner to the to-do list"))

    assert inner.calls == 0, "a model that is asked gets to invent the list"
    assert workflow.calls == [["--request", "add order toner to the to-do list", "--by", "toyota"]]
    assert "#1 order toner" in reply.text


async def test_toyota_carries_the_route_first_and_the_role_exists():
    assert TOYOTA.route_override.forced_agent(
        _prompt("add check what we spent on utilities to the to-do list")) == "company_todos"
    assert "company_todos" in ROLES and "company_todos" in TOYOTA_ROSTER.names()


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

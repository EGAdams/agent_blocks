"""The status route doesn't depend on the model agreeing to take it.

Mazda's prompt has always said every status question goes to Mickey. The 20b
often ignored that and answered from recalled conversation instead, reporting a
year of finished finance reports that do not exist. These pin the override: the
decision comes from the user's own words, before the model speaks at all.

Offline -- no LanceDB, no Groq, no Claude SDK.
"""
from agent_memory.models import AgentReply

from ..adapters.routing_model import RoutingModel
from ..adapters.status_route import StatusQuestionRoute
from ..delegation import RunnerMap, UnwiredRunner
from ..mazda_roles import MAZDA_ROSTER, ROLE_SPOKEN_NAMES
from ..profiles import MAZDA, TOYOTA
from ..progress.mickey import AGENT_NAME as MICKEY


def _prompt(message, recalled=""):
    """The shape agent_memory/prompts.py's DEFAULT_TEMPLATE actually composes."""
    return f"{recalled}\n\nUSER: {message}\nAGENT:"


class _CountingModel:
    """Answers anything, and remembers whether it was asked."""

    def __init__(self, text="Sure, happy to chat."):
        self._text = text
        self.calls = 0

    async def complete(self, prompt):
        self.calls += 1
        return AgentReply(text=self._text)


class _RecordingRunner:
    def __init__(self):
        self.ran = []

    async def run(self, role, agent_name, model, extra=""):
        self.ran.append((agent_name, extra))
        return AgentReply(text=f"[{agent_name} ran on: {extra}]")


def _routing(inner, runner, override):
    return RoutingModel(
        inner,
        model="unused",
        roster=MAZDA_ROSTER,
        runner=runner,
        speaker="Mazda",
        override=override,
    )


async def test_a_status_question_reaches_mickey_without_asking_the_model():
    inner, runner = _CountingModel(), _RecordingRunner()
    model = _routing(inner, runner, StatusQuestionRoute(MICKEY))

    reply = await model.complete(_prompt("Where do the finance reports stand?"))

    assert runner.ran == [(MICKEY, "Where do the finance reports stand?")]
    assert MICKEY in reply.text
    # Skipping the inner call is the point, not an optimisation: a model that
    # is asked gets the chance to answer the question itself from memory.
    assert inner.calls == 0


async def test_the_wordings_people_actually_use_all_count_as_status():
    route = StatusQuestionRoute(MICKEY)
    for asked in (
        "where do we stand on the reports",
        "what's the status of August",
        "give me a progress update",
        "which statements are still outstanding",
        "how far along are the 2026 reports",
        "where are we with the card statements",
        "are the September reports done",
        "what's left to do",
    ):
        assert route.forced_agent(_prompt(asked)) == MICKEY, asked


async def test_ordinary_conversation_is_left_entirely_alone():
    inner, runner = _CountingModel("Morning! What can I do?"), _RecordingRunner()
    model = _routing(inner, runner, StatusQuestionRoute(MICKEY))

    reply = await model.complete(_prompt("Good morning Mazda, how are you?"))

    assert reply.text == "Morning! What can I do?"
    assert runner.ran == []
    assert inner.calls == 1


async def test_a_recalled_turn_mentioning_status_does_not_force_a_route():
    # The composed prompt labels recalled turns USER:/AGENT: too, so matching
    # the whole thing would turn every later turn into a status question for as
    # long as that turn stayed in recall -- which is most of the conversation.
    route = StatusQuestionRoute(MICKEY)
    recalled = (
        "USER: what's the status of August\n"
        "AGENT: August hasn't been started yet."
    )
    assert route.forced_agent(_prompt("Thanks, that's helpful.", recalled)) is None


async def test_an_unmarked_prompt_forces_nothing_rather_than_guessing():
    # Some composers (RecallOnlyComposer) don't end with the USER:/AGENT:
    # marker. Without it we can't tell which words are the user's, so the
    # model decides as before -- no marker, no forced route.
    route = StatusQuestionRoute(MICKEY)
    assert route.question("where do the reports stand?") == ""
    assert route.forced_agent("where do the reports stand?") is None


async def test_toyota_has_no_override_and_mazda_does():
    # Toyota's specialists all really run, and her prompt's routing has held;
    # the override exists for the one route that must not be left to goodwill.
    assert TOYOTA.route_override is None
    assert MAZDA.route_override is not None
    assert MAZDA.route_override.forced_agent(_prompt("status update?")) == MICKEY


async def test_an_override_naming_an_agent_off_the_roster_is_ignored():
    # A typo in a profile must not break every turn -- it falls back to the
    # model deciding, which is what the route did before the override existed.
    inner, runner = _CountingModel(), _RecordingRunner()
    model = _routing(inner, runner, StatusQuestionRoute("nobody_by_that_name"))

    reply = await model.complete(_prompt("where do the reports stand?"))

    assert runner.ran == []
    assert inner.calls == 1
    assert reply.text == "Sure, happy to chat."


async def test_the_unwired_default_still_answers_honestly_through_an_override():
    # The override only chooses who; it never changes what a runner reports.
    runner = RunnerMap({}, default=UnwiredRunner(ROLE_SPOKEN_NAMES))
    model = _routing(_CountingModel(), runner, StatusQuestionRoute(MICKEY))

    reply = await model.complete(_prompt("what's the status of the reports?"))

    assert "isn't built yet" in reply.text
    assert "nothing has run" in reply.text


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

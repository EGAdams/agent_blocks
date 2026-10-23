"""The figure route, and the year it reads out of a question.

The failure these pin is specific: asked what the organization had spent,
Toyota produced a believable number. She had no way to read a real one, so the
model answered from recalled conversation -- and that reply was then stored and
recalled as history. The override takes the decision out of the model's hands,
the same way Mazda's status route does.

It is deliberately generous, because an over-eager match ends at the live
finance workflow's honest "that isn't a question I can ask the books yet"
rather than at an invented amount. What it must NOT do is swallow a request
that belongs to a more capable agent -- hence the veto tests.

Offline -- no database, no Groq, no Claude SDK.
"""
from agent_memory.models import AgentReply

from ..adapters.figure_route import FigureQuestionRoute
from ..adapters.live_finance_runner import year_of
from ..adapters.routing_model import RoutingModel
from ..delegation import RunnerMap
from ..profiles import TOYOTA
from ..roles import LIVE_BOOKS_AGENT_NAME, TOYOTA_ROSTER

ROUTE = FigureQuestionRoute(LIVE_BOOKS_AGENT_NAME)


def _prompt(message, recalled=""):
    """The shape agent_memory/prompts.py's DEFAULT_TEMPLATE actually composes."""
    return f"{recalled}\n\nUSER: {message}\nAGENT:"


class _CountingModel:
    def __init__(self, text="We spent about forty thousand dollars."):
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
        return AgentReply(text=f"[{agent_name}: {extra}]")


async def test_money_questions_reach_the_books():
    asked = [
        "how much did we spend last year",
        "how much have we paid Consumers Energy",
        "what's our total spending for 2025",
        "break it down by category",
        "show me spending by month",
        "what are our biggest categories",
        "who are our top vendors",
        "how many expenses are missing receipts",
        "which ones are unverified",
        "what's the most recent expense in the books",
        "where did the money go",
    ]
    for question in asked:
        assert ROUTE.forced_agent(_prompt(question)) == LIVE_BOOKS_AGENT_NAME, question


async def test_ordinary_conversation_is_left_to_the_model():
    for message in [
        "good morning",
        "what can you do",
        "thanks, that's all for now",
        "can you open fifth third bank so I can log in",
        "who is on your team",
    ]:
        assert ROUTE.forced_agent(_prompt(message)) is None, message


async def test_requests_belonging_to_a_more_capable_agent_are_vetoed():
    """Each of these is about real money, and each has a better home.

    The veto matters more than the match: the books answer one question as one
    spoken line, so swallowing a spreadsheet request would downgrade the work
    the user actually asked for.
    """
    elsewhere = [
        "can I get a spreadsheet of our medical spending",          # category_spreadsheet
        "build me a workbook of category spend by month",           # category_spreadsheet
        "set expense #900001 to Walgreens (RM)",                    # a WORK command
        "open the expense editor for 912345",                       # a WORK command
        "which reports exist for last month",                       # progress_check
        "how much was the training expense",                        # the practice data
    ]
    for message in elsewhere:
        assert ROUTE.forced_agent(_prompt(message)) is None, message


async def test_a_recalled_question_does_not_force_a_route_forever():
    """Only the LAST user line decides.

    Recalled turns are labelled USER: too, so matching the whole prompt would
    make every later turn a money question for as long as that turn stayed in
    recall -- the bug status_route.py was fixed for.
    """
    recalled = "USER: how much did we spend last year\nAGENT: I'll check the books."
    assert ROUTE.forced_agent(_prompt("thanks, good night", recalled)) is None


async def test_a_prompt_with_no_user_marker_forces_nothing():
    """RecallOnlyComposer composes no USER:/AGENT: pair. Force nothing."""
    assert ROUTE.forced_agent("a bare block of recalled transcript") is None


async def test_the_forced_route_skips_the_model_and_reaches_the_runner():
    inner, runner = _CountingModel(), _RecordingRunner()
    model = RoutingModel(
        inner,
        model="unused",
        roster=TOYOTA_ROSTER,
        runner=RunnerMap({LIVE_BOOKS_AGENT_NAME: runner}, default=runner),
        speaker="Toyota",
        override=ROUTE,
    )

    reply = await model.complete(_prompt("how much did we spend in 2025"))

    assert inner.calls == 0, "a model that is asked gets to invent a number"
    assert runner.ran == [(LIVE_BOOKS_AGENT_NAME, "how much did we spend in 2025")]
    assert "how much did we spend in 2025" in reply.text


async def test_toyota_carries_the_route():
    assert TOYOTA.route_override is not None
    assert TOYOTA.route_override.forced_agent(
        _prompt("what did we spend on facilities")
    ) == LIVE_BOOKS_AGENT_NAME
    assert LIVE_BOOKS_AGENT_NAME in TOYOTA.roster.names()


async def test_year_of_reads_one_year_or_none():
    assert year_of("how much did we spend in 2025") == 2025
    assert year_of("total for 2019") == 2019
    assert year_of("how much did we spend") is None
    # $2026.50 is an amount, 1,771 is a count, #900001 is a record number.
    assert year_of("was that $1,234.56") is None
    assert year_of("expense 900001") is None
    # Two different years is a comparison, which this workflow does not
    # install. Picking the first would answer a question nobody asked.
    assert year_of("compare 2025 with 2024") is None
    assert year_of("2025 spending, just 2025") == 2025


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

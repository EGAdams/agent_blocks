"""Mazda is Toyota's engine with a different profile -- these pin the
differences that matter and the honesty rule her stubs have to keep.

Offline: nothing here opens LanceDB, calls Groq, or imports the Claude SDK.
That last one is deliberate -- building Mazda's profile must not drag
finance_team in behind her (see profiles.py's _toyota_runner).
"""
import re
import tempfile

from agent_memory.models import AgentReply

from tests.fakes import ScriptedModel

from ..adapters.mickey_runner import MickeyRunner
from ..delegation import RunnerMap, UnwiredRunner
from ..mazda_roles import (
    MAZDA_CHAT_MODEL,
    MAZDA_ROSTER,
    MAZDA_SYSTEM_PROMPT,
    ROLE_SPOKEN_NAMES,
    ROLES,
    delegation_acknowledgment,
)
from ..adapters import routing_model as routing_model_module
from ..profiles import MAZDA, PROFILES, TOYOTA, profile as named_profile


class _FakeRouteOnceModel:
    def __init__(self, text):
        self._text = text

    async def complete(self, prompt):
        return AgentReply(text=self._text)


async def test_her_specialists_are_all_named_described_and_routable():
    # Roster.__post_init__ already raises on a missing entry at import, so
    # this is about the set itself, not the dicts' completeness.
    assert set(MAZDA_ROSTER.sorted_names()) == {
        "categorization", "mickey", "parser", "receipt_linker", "router",
        "vendor_identity",
        # Not a specialist -- the Open Browser ADW, routed to by name like one.
        "browser_opener",
    }
    for name in MAZDA_ROSTER.names():
        assert MAZDA_ROSTER.has(name)
        assert name in delegation_acknowledgment(name) or ROLE_SPOKEN_NAMES[name] in delegation_acknowledgment(name)


async def test_mickey_is_a_specialist_who_actually_runs():
    # Her five document specialists are Letta agents this package can't reach;
    # Mickey reads the report files herself, so routing to her must reach
    # MickeyRunner and not the "not connected yet" refusal.
    runner = MAZDA.build_runner()
    assert type(runner.run.__self__).__name__ == "RunnerMap"

    with tempfile.TemporaryDirectory() as root:
        explainer = ScriptedModel(["Two reports are missing; nothing is blocked."])
        model = routing_model_module.RoutingModel(
            _FakeRouteOnceModel("ROUTE: mickey | which statements are outstanding"),
            model="fake-role-model",
            roster=MAZDA_ROSTER,
            runner=RunnerMap(
                {"mickey": MickeyRunner(explainer=explainer, repo_root=root)},
                default=UnwiredRunner(ROLE_SPOKEN_NAMES),
            ),
            speaker="Mazda",
        )
        reply = await model.complete("where does everything stand?")

    assert reply.text == "Two reports are missing; nothing is blocked."
    # The question reached her, and the facts came off disk before the model saw them.
    assert "QUESTION: which statements are outstanding" in explainer.prompts[0]
    assert "not_started" in explainer.prompts[0]


async def test_her_stub_roles_carry_no_tools():
    # Her five specialists are real Letta agents this package can't reach yet.
    # An empty allowed_tools is what stops a stub from doing something real to
    # financial data by accident.
    for role in ROLES.values():
        assert role.allowed_tools == []


async def test_her_system_prompt_is_fully_rendered_and_lists_her_own_team():
    assert not re.search(r"\{\{\s*\w+\s*\}\}", MAZDA_SYSTEM_PROMPT)
    assert "Mazda" in MAZDA_SYSTEM_PROMPT
    for name in MAZDA_ROSTER.names():
        assert name in MAZDA_SYSTEM_PROMPT
    # Toyota's finance specialists are not hers to route to.
    assert "mapper_bank" not in MAZDA_SYSTEM_PROMPT


async def test_her_prompt_tells_her_what_she_actually_runs_on():
    # She told a user she was "built on OpenAI's GPT-4". Nothing in the prompt
    # said otherwise, so she guessed.
    # Rendered from the same constant her profile's chat_model reads, so moving
    # her to another Groq model can't leave her telling the user the old one.
    assert MAZDA_CHAT_MODEL in MAZDA_SYSTEM_PROMPT
    assert MAZDA.chat_model == MAZDA_CHAT_MODEL
    assert "claude-sonnet-5" in MAZDA_SYSTEM_PROMPT
    assert "not ChatGPT" in MAZDA_SYSTEM_PROMPT


async def test_status_questions_must_reach_mickey_and_never_come_from_memory():
    # She reported a year of finance reports as complete with no report files
    # on disk at all -- recalled chat, not a tracker reading. Mickey is the only
    # thing here that knows, so status is never hers to recall.
    lowered = MAZDA_SYSTEM_PROMPT.lower()
    assert "never state where a statement, report, or month stands unless mickey" in lowered
    assert "a status question is a route to\nmickey, every time" in lowered
    # And the "answer from this conversation yourself" licence must carve it out.
    assert "status is the exception" in lowered


async def test_her_prompt_keeps_the_words_she_asked_us_not_to_use():
    # Her own stated communication preference: plain language instead of
    # "idempotent" / "fail-closed" / "canonical". The instruction naming them
    # is allowed; using them as her vocabulary is not.
    body = MAZDA_SYSTEM_PROMPT.split("Never use the words")[0]
    for banned in ("idempotent", "fail-closed", "canonical"):
        assert banned not in body


async def test_routing_to_an_unwired_specialist_says_so_and_runs_nothing():
    model = routing_model_module.RoutingModel(
        _FakeRouteOnceModel("ROUTE: parser | pull the rows out of the January statement"),
        model="fake-role-model",
        roster=MAZDA_ROSTER,
        runner=UnwiredRunner(ROLE_SPOKEN_NAMES),
        speaker="Mazda",
    )
    reply = await model.complete("get the rows out of January")

    assert "our statement parser" in reply.text
    assert "nothing has run" in reply.text
    # The whole point: she must not imply the work happened.
    assert "done" not in reply.text.replace("it was done when it wasn't", "")
    assert reply.cost_usd in (0.0, None)


async def test_an_unknown_specialist_name_is_declined_not_crashed():
    model = routing_model_module.RoutingModel(
        _FakeRouteOnceModel("ROUTE: mapper_bank"),
        model="fake-role-model",
        roster=MAZDA_ROSTER,
        runner=UnwiredRunner(ROLE_SPOKEN_NAMES),
        speaker="Mazda",
    )
    reply = await model.complete("check the bank payments")

    assert "mapper_bank" in reply.text
    assert "parser" in reply.text  # it names who she does have


async def test_the_two_receptionists_stay_separable():
    assert named_profile("mazda") is MAZDA
    assert set(PROFILES) == {"toyota", "mazda"}
    # Each hears what the user told the other, through one shared table.
    assert MAZDA.peer_session == TOYOTA.name
    assert TOYOTA.peer_session == MAZDA.name
    # Their archives don't share a directory, and they don't sound alike.
    assert MAZDA.nexus_subdir != TOYOTA.nexus_subdir
    assert MAZDA.tts_voice == "en-US-AriaNeural"
    assert TOYOTA.tts_voice is None  # the dashboard's own en-GB-SoniaNeural
    # OPEN_AGENT and the operations/WorkflowModel stack are Toyota's only.
    assert (MAZDA.handoff, MAZDA.operations) == (False, False)
    assert (TOYOTA.handoff, TOYOTA.operations) == (True, True)


async def test_an_unknown_agent_name_names_the_alternatives():
    try:
        named_profile("skoda")
    except ValueError as exc:
        assert "toyota" in str(exc) and "mazda" in str(exc)
    else:
        raise AssertionError("an unknown --agent value must not resolve")


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

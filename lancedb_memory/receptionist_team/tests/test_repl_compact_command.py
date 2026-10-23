"""repl()'s typed/voice loop must offer a way to compact the conversation
context -- the dashboard already has this via POST /compact -> agent.
clear_context() (see service.py, turn_runner.py, CLAUDE.md's "The sidecar"),
but the REPL front end (`main.py`) had no equivalent trigger: every typed
line went straight to handle_turn(), so a long CLI session could only grow
its context, never reduce it.

A bare "/compact" line (case-insensitive, surrounding whitespace ignored) now
calls agent.clear_context() instead of handle_turn(), prints the new session
id, and -- in --voice mode -- speaks a short confirmation. It must never reach
handle_turn(), since clear_context() is a local, free operation (no Groq call,
no persisted turn).

"/compact" alone is not reachable with your hands off the keyboard: voice
input is transcribed speech, and a mic never produces a literal slash.
`_is_compact_command` (main.py) also recognizes natural spoken equivalents --
"compact the conversation", "clear your memory", etc -- so a --voice session
can trigger the same clear_context() path by saying it, not typing it.
Matched with `re.fullmatch` (not `search`), same rule as RoutingModel's ROUTE
line: ordinary conversation that merely mentions "context" must not misfire.

Offline: build_receptionist and DashboardVoiceIO are both monkeypatched to fakes, so
this never touches LanceDB, Groq, or the dashboard's voice endpoints -- same
pattern as test_repl_voice_delegation.py.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_repl_compact_command
"""
from agent_memory.config import Settings
from agent_memory.models import AgentReply

from .. import main as main_module


class _FakeCost:
    total_cost_usd = 0.0
    total_input_tokens = 0
    total_output_tokens = 0


class _FakeAgent:
    def __init__(self):
        self.handle_turn_calls = []
        self.clear_context_calls = 0

    async def handle_turn(self, text, request_id=None):
        self.handle_turn_calls.append(text)
        return AgentReply(text="ok")

    def clear_context(self):
        self.clear_context_calls += 1
        return f"toyota-test--ctx{self.clear_context_calls}"


class _FakeVoiceIO:
    instances = []

    def __init__(self, *args, **kwargs):
        self.spoken = []
        self._lines = iter(["/compact"])
        _FakeVoiceIO.instances.append(self)

    def listen(self):
        try:
            return next(self._lines)
        except StopIteration:
            # End the REPL loop, same as ctrl-c at the prompt.
            raise EOFError

    def speak(self, text):
        self.spoken.append(text)


def _patch(monkeypatch_target, name, value):
    original = getattr(monkeypatch_target, name)
    setattr(monkeypatch_target, name, value)
    return original


async def test_typed_slash_compact_clears_context_without_a_turn():
    fake_agent = _FakeAgent()

    def fake_build_receptionist(profile, settings, role_model, on_delegation=None, on_work=None, **kwargs):
        return fake_agent, _FakeCost()

    inputs = iter(["/compact"])

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    original_build_receptionist = _patch(main_module, "build_receptionist", fake_build_receptionist)
    original_input = _patch(__import__("builtins"), "input", fake_input)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(main_module.TOYOTA, settings, role_model="fake-role-model", voice=False)
    finally:
        main_module.build_receptionist = original_build_receptionist
        __import__("builtins").input = original_input

    assert fake_agent.clear_context_calls == 1, fake_agent.clear_context_calls
    assert fake_agent.handle_turn_calls == [], fake_agent.handle_turn_calls


async def test_voice_slash_compact_speaks_a_confirmation():
    _FakeVoiceIO.instances = []
    fake_agent = _FakeAgent()

    def fake_build_receptionist(profile, settings, role_model, on_delegation=None, on_work=None, **kwargs):
        return fake_agent, _FakeCost()

    original_build_receptionist = _patch(main_module, "build_receptionist", fake_build_receptionist)
    original_voice_io = _patch(main_module, "DashboardVoiceIO", _FakeVoiceIO)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(main_module.TOYOTA, settings, role_model="fake-role-model", voice=True)
    finally:
        main_module.build_receptionist = original_build_receptionist
        main_module.DashboardVoiceIO = original_voice_io

    assert fake_agent.clear_context_calls == 1, fake_agent.clear_context_calls
    assert fake_agent.handle_turn_calls == [], fake_agent.handle_turn_calls
    voice_io = _FakeVoiceIO.instances[0]
    assert voice_io.spoken, "compacting in --voice mode should speak a confirmation"


async def test_spoken_natural_phrase_clears_context_without_a_turn():
    # No keyboard involved: the "transcript" is a natural sentence a person
    # would actually say, not the literal "/compact" string.
    _FakeVoiceIO.instances = []
    fake_agent = _FakeAgent()

    def fake_build_receptionist(profile, settings, role_model, on_delegation=None, on_work=None, **kwargs):
        return fake_agent, _FakeCost()

    class _NaturalPhraseVoiceIO(_FakeVoiceIO):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._lines = iter(["Compact the conversation, please."])

    original_build_receptionist = _patch(main_module, "build_receptionist", fake_build_receptionist)
    original_voice_io = _patch(main_module, "DashboardVoiceIO", _NaturalPhraseVoiceIO)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(main_module.TOYOTA, settings, role_model="fake-role-model", voice=True)
    finally:
        main_module.build_receptionist = original_build_receptionist
        main_module.DashboardVoiceIO = original_voice_io

    assert fake_agent.clear_context_calls == 1, fake_agent.clear_context_calls
    assert fake_agent.handle_turn_calls == [], fake_agent.handle_turn_calls
    voice_io = _NaturalPhraseVoiceIO.instances[0]
    assert voice_io.spoken, "a spoken compact phrase should speak a confirmation too"


async def test_a_sentence_merely_mentioning_context_does_not_misfire():
    # Guard against the obvious regression: loosening the match to cover
    # natural speech must not let ordinary conversation about "context" or
    # "memory" silently wipe the conversation.
    fake_agent = _FakeAgent()

    def fake_build_receptionist(profile, settings, role_model, on_delegation=None, on_work=None, **kwargs):
        return fake_agent, _FakeCost()

    inputs = iter(["Can you remember the context of what we discussed?"])

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    original_build_receptionist = _patch(main_module, "build_receptionist", fake_build_receptionist)
    original_input = _patch(__import__("builtins"), "input", fake_input)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(main_module.TOYOTA, settings, role_model="fake-role-model", voice=False)
    finally:
        main_module.build_receptionist = original_build_receptionist
        __import__("builtins").input = original_input

    assert fake_agent.clear_context_calls == 0, fake_agent.clear_context_calls
    assert fake_agent.handle_turn_calls == ["Can you remember the context of what we discussed?"]


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

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

Offline: build_toyota and DashboardVoiceIO are both monkeypatched to fakes, so
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

    def fake_build_toyota(settings, role_model, on_delegation=None, on_work=None):
        return fake_agent, _FakeCost()

    inputs = iter(["/compact"])

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    original_build_toyota = _patch(main_module, "build_toyota", fake_build_toyota)
    original_input = _patch(__import__("builtins"), "input", fake_input)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(settings, role_model="fake-role-model", voice=False)
    finally:
        main_module.build_toyota = original_build_toyota
        __import__("builtins").input = original_input

    assert fake_agent.clear_context_calls == 1, fake_agent.clear_context_calls
    assert fake_agent.handle_turn_calls == [], fake_agent.handle_turn_calls


async def test_voice_slash_compact_speaks_a_confirmation():
    _FakeVoiceIO.instances = []
    fake_agent = _FakeAgent()

    def fake_build_toyota(settings, role_model, on_delegation=None, on_work=None):
        return fake_agent, _FakeCost()

    original_build_toyota = _patch(main_module, "build_toyota", fake_build_toyota)
    original_voice_io = _patch(main_module, "DashboardVoiceIO", _FakeVoiceIO)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(settings, role_model="fake-role-model", voice=True)
    finally:
        main_module.build_toyota = original_build_toyota
        main_module.DashboardVoiceIO = original_voice_io

    assert fake_agent.clear_context_calls == 1, fake_agent.clear_context_calls
    assert fake_agent.handle_turn_calls == [], fake_agent.handle_turn_calls
    voice_io = _FakeVoiceIO.instances[0]
    assert voice_io.spoken, "compacting in --voice mode should speak a confirmation"


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

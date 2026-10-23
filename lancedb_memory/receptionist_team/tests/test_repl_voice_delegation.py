"""repl()'s --voice path must wire the acknowledgment speaker into build_receptionist's
on_delegation hook, so a delegation acknowledgment is spoken immediately
instead of going silent for the whole delegation (see CLAUDE.md, "The
delegation acknowledgment, and what is still only half-wired").

Offline: build_receptionist and DashboardVoiceIO are both monkeypatched to fakes,
so this never touches LanceDB, Groq, or the dashboard's voice endpoints.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_repl_voice_delegation
"""
from agent_memory.config import Settings
from agent_memory.models import AgentReply

from .. import main as main_module
from ..adapters.voice_io import AcknowledgmentSpeaker


class _FakeCost:
    total_cost_usd = 0.0
    total_input_tokens = 0
    total_output_tokens = 0


class _FakeAgent:
    async def handle_turn(self, text, request_id=None):
        return AgentReply(text="ok")


class _FakeVoiceIO:
    instances = []

    def __init__(self, *args, **kwargs):
        self.spoken = []
        _FakeVoiceIO.instances.append(self)

    def listen(self):
        # End the REPL loop immediately, same as ctrl-c at the prompt.
        raise EOFError

    def speak(self, text):
        self.spoken.append(text)


def _patch(monkeypatch_target, name, value):
    original = getattr(monkeypatch_target, name)
    setattr(monkeypatch_target, name, value)
    return original


async def test_voice_mode_wires_speak_into_on_delegation():
    _FakeVoiceIO.instances = []
    captured = {}

    def fake_build_receptionist(profile, settings, role_model, on_delegation=None, on_work=None, **kwargs):
        captured["on_delegation"] = on_delegation
        return _FakeAgent(), _FakeCost()

    original_build_receptionist = _patch(main_module, "build_receptionist", fake_build_receptionist)
    original_voice_io = _patch(main_module, "DashboardVoiceIO", _FakeVoiceIO)
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(main_module.TOYOTA, settings, role_model="fake-role-model", voice=True)
    finally:
        main_module.build_receptionist = original_build_receptionist
        main_module.DashboardVoiceIO = original_voice_io

    assert "on_delegation" in captured
    assert captured["on_delegation"] is not None, "voice mode left on_delegation=None"

    voice_io = _FakeVoiceIO.instances[0]
    sink = captured["on_delegation"]
    # The sink is AcknowledgmentSpeaker.say, not voice_io.speak: say() hands
    # playback to a background thread so the delegation can start immediately.
    # wait() is what the reply path uses to join it, so the test joins too
    # rather than racing the thread.
    assert isinstance(sink.__self__, AcknowledgmentSpeaker)
    sink("let me check with accounting")
    sink.__self__.wait()
    assert voice_io.spoken == ["let me check with accounting"], voice_io.spoken


async def test_typed_mode_leaves_on_delegation_none():
    """No voice_io exists in typed mode, so there is nothing to wire in --
    on_delegation must stay None, same as before this change."""
    captured = {}

    def fake_build_receptionist(profile, settings, role_model, on_delegation=None, on_work=None, **kwargs):
        captured["on_delegation"] = on_delegation
        return _FakeAgent(), _FakeCost()

    original_build_receptionist = _patch(main_module, "build_receptionist", fake_build_receptionist)
    original_input = _patch(__import__("builtins"), "input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    try:
        settings = Settings(session_id="toyota-test", model="fake", embedding_profile="hash")
        await main_module.repl(main_module.TOYOTA, settings, role_model="fake-role-model", voice=False)
    finally:
        main_module.build_receptionist = original_build_receptionist
        __import__("builtins").input = original_input

    assert captured["on_delegation"] is None


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

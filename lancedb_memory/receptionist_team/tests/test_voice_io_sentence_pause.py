"""DashboardVoiceIO.speak() pipelines sentence synthesis so playback starts
fast, and plays each sentence's audio immediately after the previous one
finishes -- no artificial pause is inserted between sentences. This pins
that `speak()` never sleeps between (or after) sentences, so a
multi-sentence reply is heard as one continuous utterance rather than a
string of clips with gaps of dead air.

Offline: _synthesize/_play/shutil.which/time.sleep are all monkeypatched, so
this never touches the dashboard's TTS endpoint, a real player binary, or an
actual wall-clock pause.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_voice_io_sentence_pause
"""
from ..adapters import voice_io as voice_io_module
from ..adapters.voice_io import DashboardVoiceIO


def _patch(target, name, value):
    original = getattr(target, name)
    setattr(target, name, value)
    return original


async def test_speak_has_no_pause_between_sentences():
    played = []
    slept = []

    def fake_synthesize(self, index, sentence):
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        played.append(index)

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        slept.append(seconds)

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("First sentence. Second sentence. Third sentence.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert played == [0, 1, 2], played
    # No pause anywhere -- not between sentences, not after the last one.
    # Sentences should play back to back with no noticeable gap.
    assert slept == [], slept


async def test_speak_single_sentence_has_no_pause():
    played = []
    slept = []

    def fake_synthesize(self, index, sentence):
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        played.append(index)

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        slept.append(seconds)

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("Just one sentence.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert played == [0], played
    assert slept == [], slept


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

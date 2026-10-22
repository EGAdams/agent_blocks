"""DashboardVoiceIO.speak() splits text into sentences so it can pipeline
synthesis (see voice_io.py's docstring for `speak`). The split was a bare
`(?<=[.!?])\\s+` regex, which treats "Dr. Smith" as two sentences -- the
period after "Dr" looks exactly like a sentence end to that regex. That
produces a spurious extra synthesize call mid-name. This pins that a
trailing abbreviation ("Dr.", "Mr.", "vs.",
"etc.", ...) does not end a sentence, while a real sentence boundary right
after one still does.

Offline: _synthesize/_play/shutil.which are monkeypatched, same as
test_voice_io_sentence_pause.py.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_voice_io_abbreviation_split
"""
from ..adapters import voice_io as voice_io_module
from ..adapters.voice_io import DashboardVoiceIO


def _patch(target, name, value):
    original = getattr(target, name)
    setattr(target, name, value)
    return original


async def test_speak_does_not_split_after_a_title_abbreviation():
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("Dr. Smith will see you. Please wait in the lobby.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "Dr. Smith will see you.",
        "Please wait in the lobby.",
    ], synthesized


async def test_speak_merges_abbreviation_but_still_splits_the_next_real_sentence():
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        # "Mr." must not split from "Lee...", but the real sentence boundary
        # right after it still has to split -- the merge is per-abbreviation,
        # not "stop splitting for the rest of the utterance."
        io.speak("Please see Mr. Lee for billing. He is in room four.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "Please see Mr. Lee for billing.",
        "He is in room four.",
    ], synthesized


async def test_speak_does_not_split_after_a_time_abbreviation():
    """"a.m."/"p.m." trip the same bug "Dr." did: `_TRAILING_WORD_RE` only
    sees the run of word chars right before the final period, which for
    "a.m." is just "m" (the first period breaks the \\w+ run) -- so a
    receptionist reading back an appointment time split "9 a.m." into two
    synthesize calls with a spurious pause between the time and the rest of
    the sentence it belongs to."""
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("The meeting is at 9 a.m. tomorrow morning.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "The meeting is at 9 a.m. tomorrow morning.",
    ], synthesized


async def test_speak_does_not_split_after_etc():
    """"etc." is listed in this file's own module docstring (and this
    module's _ABBREVIATIONS comment) as a covered abbreviation, but "etc"
    was never actually added to the `_ABBREVIATIONS` set -- so a reply like
    "Bring your ID, insurance card, etc. before your appointment." got a
    spurious extra synthesize call split right after "etc.", the same bug
    "a.m."/"p.m." had before they were added, instead of being read as one
    unbroken sentence."""
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("Bring your ID, insurance card, etc. before your appointment.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "Bring your ID, insurance card, etc. before your appointment.",
    ], synthesized


async def test_speak_does_not_split_after_a_single_letter_initial():
    """The generic version of the "Dr."/"etc." bug: `_ABBREVIATIONS` is a
    fixed word list, so it can only cover abbreviations someone thought to
    add. A single-letter initial like "J." in "Contact J. Smith" is not on
    that list (and never could be -- any letter can be an initial), so
    `_TRAILING_WORD_RE` finds "j", `_ABBREVIATIONS` rejects it, and
    `_split_sentences` splits right after the initial, giving the receptionist
    a spurious extra synthesize call -- and a spoken pause -- mid-name. The
    fix must be general (any single letter before a period never ends a
    sentence), not another entry in the fixed list."""
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("Contact J. Smith for details. He will reply soon.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "Contact J. Smith for details.",
        "He will reply soon.",
    ], synthesized


async def test_speak_does_not_split_after_multiple_single_letter_initials():
    """Two initials in a row ("U.S.") must not split either -- each merge
    only undoes the one split that followed a single-letter piece, so a run
    of several has to keep merging rather than stopping after the first."""
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("The U.S. economy grew today. Rates may rise.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "The U.S. economy grew today.",
        "Rates may rise.",
    ], synthesized


async def test_speak_does_not_split_after_a_month_abbreviation():
    """A finance receptionist routinely reads month ranges back ("Jan.
    through Dec.", "the Mar. statement") -- but "jan"/"dec"/etc. were never
    added to `_ABBREVIATIONS`, so each one tripped the same bug "Dr." had:
    `_TRAILING_WORD_RE` finds a multi-letter word that isn't on the fixed
    list and isn't a single-letter initial, so `_split_sentences` treats it
    as a real sentence end and gives the receptionist a spurious extra
    synthesize call -- and a spoken pause -- mid-range. Two abbreviations in
    a row ("Jan." ... "Dec.") must each be merged, not just the first."""
    synthesized = []

    def fake_synthesize(self, index, sentence):
        synthesized.append(sentence)
        return b"fake-audio"

    def fake_play(self, index, payload, player):
        pass

    def fake_which(name):
        return "ffplay" if name == "ffplay" else None

    def fake_sleep(seconds):
        pass

    original_synth = _patch(DashboardVoiceIO, "_synthesize", fake_synthesize)
    original_play = _patch(DashboardVoiceIO, "_play", fake_play)
    original_which = _patch(voice_io_module.shutil, "which", fake_which)
    original_sleep = _patch(voice_io_module.time, "sleep", fake_sleep)
    try:
        io = DashboardVoiceIO()
        io.speak("The report covers Jan. through Dec. of this year. We will send it Friday.")
    finally:
        DashboardVoiceIO._synthesize = original_synth
        DashboardVoiceIO._play = original_play
        voice_io_module.shutil.which = original_which
        voice_io_module.time.sleep = original_sleep

    assert synthesized == [
        "The report covers Jan. through Dec. of this year.",
        "We will send it Friday.",
    ], synthesized


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))

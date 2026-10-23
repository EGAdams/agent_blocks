"""DashboardVoiceIO -- reuses the dashboard's already-built voice pipeline
instead of re-implementing speech I/O for this CLI:

- STT: POST /api/voice (whisper.cpp/Groq transcription + cleanup, same path
  the browser's push-to-talk uses) -- dashboard/http_app/post_routes.py.
- TTS: POST /api/tts (server-side edge-tts, en-GB-SoniaNeural unless a
  `voice` is given) --
  same endpoint dashboard/js/implementation/edge-tts-speech-synthesizer.js calls.

Requires dashboard-server.service running locally (localhost:8765). This
adapter only ever talks HTTP to it -- it does not import anything from
dashboard/, so a dashboard-side refactor can't break this package's imports.

**Recording is silence-terminated, not fixed-duration.** WSLg exposes the mic
as a PulseAudio source (`RDPSource`), not an ALSA card -- `arecord` only
worked at all via the `~/.asoundrc` ALSA-to-Pulse shim, with no way to see
audio levels as they arrive. `parecord --raw` streams raw PCM directly from
the same Pulse server, so `_record_until_silence()` can watch it frame by
frame: it waits for real speech to start (skipping ambient noise below an
adaptive floor), then stops the instant the user pauses -- one complete
thought per turn, not a guessed window that's either too short (cuts a
sentence off) or too long (dead air the user has to sit through, mistaken
here for "I have to press enter").
"""
import array
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .metrics import Stopwatch, log_stage

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Title/initialism abbreviations whose trailing "." is not a sentence end --
# left alone, `_SENTENCE_SPLIT_RE` treats "Dr. Smith" as two sentences and
# `speak()` makes a spurious extra synthesize call mid-name. `re`
# lookbehind must be fixed-width, so one regex can't skip these (they aren't
# all the same length); `_split_sentences` re-merges them after the split
# instead.
#
# "m" covers "a.m."/"p.m.": `_TRAILING_WORD_RE` only sees the run of word
# chars right before the *final* period, and the first period in "a.m."
# already breaks that run -- so for both "a.m." and "p.m." the captured
# fragment is just "m", never "am"/"pm". A receptionist reading an
# appointment time back ("at 9 a.m. tomorrow") would otherwise get a
# spurious synthesize call split right after the time.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "jr", "sr", "st", "prof", "rev", "gen", "col",
    "capt", "lt", "sgt", "no", "vs", "approx", "ave", "blvd", "inc", "ltd",
    "co", "corp", "m", "etc",
    # Month abbreviations -- a finance receptionist routinely reads date
    # ranges and report periods back ("the Jan. through Dec. totals", "the
    # Mar. statement"), and each one tripped the same bug "Dr." did before
    # it was added: a multi-letter trailing word that is a real abbreviation
    # but wasn't on the fixed list, so `_split_sentences` treated it as a
    # sentence end and gave a spurious extra synthesize call -- and a
    # spoken pause -- mid-range. "May" is a whole word, never abbreviated.
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
})
_TRAILING_WORD_RE = re.compile(r"(\w+)\.$")


def _split_sentences(text: str) -> list[str]:
    """Split `text` into sentences, then re-merge any split that only
    happened because the piece before it ended in a known abbreviation
    (e.g. "Dr.") -- or in a single-letter initial (e.g. "J.", "U.S.") --
    rather than a real sentence end. `_ABBREVIATIONS` is necessarily a fixed
    word list and can never cover every initial a name or acronym might use
    ("J. Smith", "U.S.", "U.K."), so any single letter before the period is
    treated as an initial generically, on top of the fixed list. A run of
    several initials in a row ("U.S.") keeps re-merging one piece at a time
    rather than stopping after the first. Only that one join is undone each
    time -- a genuine sentence boundary right after the merged piece still
    splits normally."""
    pieces = [p for p in _SENTENCE_SPLIT_RE.split(text) if p]
    sentences: list[str] = []
    for piece in pieces:
        if sentences:
            match = _TRAILING_WORD_RE.search(sentences[-1])
            if match and (
                len(match.group(1)) == 1 or match.group(1).lower() in _ABBREVIATIONS
            ):
                sentences[-1] = f"{sentences[-1]} {piece}"
                continue
        sentences.append(piece)
    return sentences

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes -- S16LE
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * SAMPLE_WIDTH

WARMUP_MS = 400               # discard: parecord/RDPSource ramps up from ~0 to its real
                               # noise floor over this window -- calibrating during it
                               # would mistake the ramp for silence
CALIBRATION_MS = 600          # ambient-noise sample AFTER warm-up, before listening for speech
SPEECH_MARGIN = 1200          # RMS above the calibrated floor that counts as speech --
                               # this box's mic (WSLg RDPSource) idles at ~1700-2400 RMS,
                               # not near-zero, and swings ~700+ within that steady state
FLOOR_DECAY = 0.98            # once calibrated, floor keeps tracking ambient drift while
                               # not speaking (0.98 old + 0.02 new per frame) so a fan/hum
                               # change mid-turn doesn't wedge the threshold
END_SILENCE_MS = 900          # pause length that means "that was a complete thought"
MAX_WAIT_FOR_SPEECH_MS = 8000  # give up (empty utterance) if nobody spoke at all
MAX_UTTERANCE_MS = 20000      # hard safety cap so one turn can never hang forever


def _rms(chunk: bytes) -> float:
    """`audioop.rms` was removed in Python 3.13 (PEP 594) -- this is the
    same computation over a signed 16-bit little-endian frame."""
    samples = array.array("h")
    samples.frombytes(chunk)
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


class VoiceUnavailableError(RuntimeError):
    """Fails closed: voice I/O never silently substitutes a guess for a real
    transcript or a skipped reply -- callers fall back to typed input/output."""


class DashboardVoiceIO:
    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        pipecat_agent_id: str | None = None,
        voice: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # None leaves the voice out of the /api/tts payload entirely, so the
        # dashboard picks its own default (en-GB-SoniaNeural) -- that is
        # Toyota. A receptionist who wants to sound different names her voice
        # here instead (see profiles.py's tts_voice).
        self._voice = voice
        # Requesting the pipecat backend routes STT through Groq (~1s) instead of
        # the default local whisper.cpp pass (~25s on this CPU) -- the dashboard
        # server only allows it for this one pilot agent id, set as an env var
        # here rather than duplicating it as a hardcoded id.
        self._pipecat_agent_id = pipecat_agent_id or os.environ.get("PIPECAT_PILOT_AGENT_ID")

    def listen(self) -> str:
        """Record one silence-terminated utterance and return its cleaned transcript.

        Returns "" if nothing was said within MAX_WAIT_FOR_SPEECH_MS -- that is
        not an error, it just means the caller should listen again.
        """
        audio = self._record_until_silence()
        if audio is None:
            return ""

        print("[transcribing...]")
        headers = {"X-Filename": "audio.wav", "Content-Type": "audio/wav"}
        if self._pipecat_agent_id:
            headers["X-Voice-Media-Backend"] = "pipecat"
            headers["X-Voice-Agent-Id"] = self._pipecat_agent_id
        request = urllib.request.Request(
            f"{self._base_url}/api/voice",
            data=audio,
            headers=headers,
            method="POST",
        )
        try:
            with Stopwatch("stt_request", detail=f"audio_bytes={len(audio)}"):
                with urllib.request.urlopen(request, timeout=60) as response:
                    result = json.loads(response.read().decode())
        except (urllib.error.URLError, OSError) as exc:
            raise VoiceUnavailableError(f"dashboard STT request failed: {exc}") from exc

        if not result.get("ok"):
            raise VoiceUnavailableError(result.get("error") or "transcription failed")
        return result.get("cleaned_text") or result.get("raw_transcript") or ""

    def _record_until_silence(self) -> bytes | None:
        """`parecord ... -` (stdout pipe) buffers internally and only flushes
        in bursts -- fine for a fixed-duration recording, useless for reading
        levels in real time. Recording to a real file instead: PulseAudio
        flushes that incrementally (confirmed: growing file size every ~1s of
        wall time), so polling it is an honest proxy for "what just got said."
        """
        if not shutil.which("parecord"):
            raise VoiceUnavailableError(
                "parecord not found on PATH -- WSLg exposes the mic over PulseAudio; "
                "install pulseaudio-utils."
            )

        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
            raw_path = Path(f.name)

        proc = subprocess.Popen(
            ["parecord", "--raw", "--channels=1", f"--rate={SAMPLE_RATE}", "--format=s16le", str(raw_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        floor = 0.0
        warmup_frames = max(1, WARMUP_MS // FRAME_MS)
        calibration_frames = max(1, CALIBRATION_MS // FRAME_MS)
        frame_count = 0
        speaking = False
        silence_ms = 0
        elapsed_ms = 0
        poll_seconds = FRAME_MS / 1000
        print("[listening...]")
        try:
            with open(raw_path, "rb") as reader:
                while True:
                    if proc.poll() is not None:
                        # parecord died on its own (e.g. PulseAudio unreachable) --
                        # left silently as DEVNULL, this used to look exactly like
                        # "nobody spoke" for the full MAX_WAIT_FOR_SPEECH_MS, every
                        # single turn, with no indication the mic was never live.
                        stderr = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
                        raw_path.unlink(missing_ok=True)
                        raise VoiceUnavailableError(
                            f"parecord exited unexpectedly (mic/PulseAudio unavailable): "
                            f"{stderr or 'no error output'}"
                        )
                    time.sleep(poll_seconds)
                    elapsed_ms += FRAME_MS
                    chunk = reader.read()
                    # New audio may arrive in any size; walk it in FRAME_BYTES
                    # slices so silence/speech decisions stay on a fixed clock
                    # regardless of how PulseAudio happened to batch this poll.
                    for i in range(0, len(chunk) - FRAME_BYTES + 1, FRAME_BYTES):
                        frame_count += 1
                        level = _rms(chunk[i : i + FRAME_BYTES])

                        if frame_count <= warmup_frames:
                            continue  # discard the source's startup ramp entirely
                        if frame_count <= warmup_frames + calibration_frames:
                            floor = max(floor, level)
                            continue

                        if level > floor + SPEECH_MARGIN:
                            speaking = True
                            silence_ms = 0
                        elif speaking:
                            silence_ms += FRAME_MS
                        else:
                            # still ambient -- keep the floor tracking real drift
                            floor = FLOOR_DECAY * floor + (1 - FLOOR_DECAY) * level

                    if speaking and silence_ms >= END_SILENCE_MS:
                        break
                    if not speaking and elapsed_ms >= MAX_WAIT_FOR_SPEECH_MS:
                        break
                    if elapsed_ms >= MAX_UTTERANCE_MS:
                        break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

        if not speaking:
            raw_path.unlink(missing_ok=True)
            return None

        raw_bytes = raw_path.read_bytes()
        raw_path.unlink(missing_ok=True)

        # Trim the trailing pause back to a little breathing room instead of
        # shipping the whole END_SILENCE_MS gap to the transcriber.
        trailing_pad_bytes = (END_SILENCE_MS // 3) * SAMPLE_RATE * SAMPLE_WIDTH // 1000
        trim_bytes = END_SILENCE_MS * SAMPLE_RATE * SAMPLE_WIDTH // 1000 - trailing_pad_bytes
        if len(raw_bytes) > trim_bytes:
            raw_bytes = raw_bytes[:-trim_bytes]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
        try:
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(SAMPLE_WIDTH)
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(raw_bytes)
            return wav_path.read_bytes()
        finally:
            wav_path.unlink(missing_ok=True)

    def speak(self, text: str) -> None:
        """Synthesize `text` via the dashboard's edge-tts endpoint and play it,
        one sentence at a time. /api/tts fully renders its whole input before
        replying -- for a long reply that means a long silent wait before
        anything is heard. Splitting into sentences and prefetching sentence
        N+1's audio while sentence N plays turns that into "wait for one
        sentence" instead, and hides the rest of the synthesis time behind
        playback that's already happening. Sentences play back to back with
        no inserted pause between them, so a multi-sentence reply sounds
        continuous instead of leaving a noticeable gap after every period.

        Fails closed: a broken player/request only skips playback, never
        crashes the conversation -- the reply is always already printed to
        the console. A sentence that fails to synthesize is skipped, not
        fatal to the rest of the utterance.
        """
        if not text.strip():
            return
        player = shutil.which("ffplay") or shutil.which("mpg123") or shutil.which("paplay")
        if not player:
            print("[voice output unavailable: none of ffplay/mpg123/paplay on PATH]")
            return

        sentences = _split_sentences(text.strip())
        if not sentences:
            return

        print("[synthesizing speech...]")
        with (
            Stopwatch("tts_total", detail=f"sentences={len(sentences)} chars={len(text)}"),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            pending = pool.submit(self._synthesize, 0, sentences[0])
            for i, sentence in enumerate(sentences):
                # If this blocks for longer than ~0, sentence i+1's synthesis
                # wasn't ready by the time sentence i finished playing --
                # tts_wait_stall > 0 pinpoints exactly which sentence fell behind.
                with Stopwatch("tts_wait_stall", detail=f"sentence={i}"):
                    audio = pending.result()
                if i + 1 < len(sentences):
                    pending = pool.submit(self._synthesize, i + 1, sentences[i + 1])
                if audio is not None:
                    self._play(i, audio, player)

    def _synthesize(self, index: int, sentence: str) -> bytes | None:
        """One /api/tts round trip for a single sentence. Runs on the
        pipelining thread -- prints its own failures instead of raising, so
        one bad sentence can't take down the executor or the rest of the
        utterance."""
        request = urllib.request.Request(
            f"{self._base_url}/api/tts",
            data=json.dumps(
                {"text": sentence, "voice": self._voice} if self._voice else {"text": sentence}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with Stopwatch("tts_synthesize", detail=f"sentence={index} chars={len(sentence)}"):
                with urllib.request.urlopen(request, timeout=30) as response:
                    content_type = response.headers.get("Content-Type", "")
                    payload = response.read()
        except (urllib.error.URLError, OSError) as exc:
            print(f"[voice output failed: {exc}]")
            return None

        if "audio" not in content_type:
            error = json.loads(payload.decode()).get("error", "unknown TTS error")
            print(f"[voice output failed: {error}]")
            return None
        return payload

    def _play(self, index: int, payload: bytes, player: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(payload)
            mp3_path = f.name
        try:
            args = (
                [player, "-nodisp", "-autoexit", "-loglevel", "quiet", mp3_path]
                if "ffplay" in player
                else [player, mp3_path]
            )
            with Stopwatch("tts_play", detail=f"sentence={index} bytes={len(payload)} player={player}"):
                try:
                    # stdin=DEVNULL: ffplay polls stdin for keypress commands
                    # ('q' to quit) even with -nodisp -- inheriting the
                    # terminal's stdin here risks it stopping on SIGTTIN and
                    # hanging indefinitely instead of exiting on -autoexit.
                    # timeout is a fail-closed backstop against exactly that:
                    # one stuck sentence must not block the rest of the reply.
                    subprocess.run(
                        args, stdin=subprocess.DEVNULL, capture_output=True, timeout=30
                    )
                except subprocess.TimeoutExpired:
                    print(f"[voice output timed out: sentence {index}]")
        finally:
            Path(mp3_path).unlink(missing_ok=True)


class AcknowledgmentSpeaker:
    """Play a delegation acknowledgment while the delegated work runs.

    `DashboardVoiceIO.speak` is fully blocking -- its ThreadPoolExecutor is a
    context manager (shutdown waits) and `_play` shells out to a blocking
    subprocess.run. So calling it straight from RoutingModel's on_delegation
    sink would serialize "please wait..." in front of the delegation and buy
    nothing; the point is to overlap them. Playback therefore goes to a
    background thread, and `wait()` joins it before the final reply is spoken
    so two players never talk over each other.
    """

    def __init__(self, voice_io: DashboardVoiceIO) -> None:
        self._voice_io = voice_io
        self._thread: threading.Thread | None = None

    def say(self, sentence: str) -> None:
        """Start playing `sentence`; return immediately."""
        self.wait()  # one acknowledgment at a time, never two overlapping
        thread = threading.Thread(target=self._speak, args=(sentence,), daemon=True)
        self._thread = thread
        thread.start()

    def wait(self) -> None:
        """Block until any in-flight acknowledgment has finished playing."""
        if self._thread is None:
            return
        self._thread.join()
        self._thread = None

    def _speak(self, sentence: str) -> None:
        # Fails closed, like every other voice path here: an acknowledgment
        # that can't be played must not kill the turn it belongs to, and an
        # exception on this thread would otherwise surface as a bare traceback.
        try:
            self._voice_io.speak(sentence)
        except Exception as exc:
            print(f"[acknowledgment playback failed: {type(exc).__name__}: {exc}]")

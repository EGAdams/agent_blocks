"""DashboardVoiceIO -- reuses the dashboard's already-built voice pipeline
instead of re-implementing speech I/O for this CLI:

- STT: POST /api/voice (whisper.cpp/Groq transcription + cleanup, same path
  the browser's push-to-talk uses) -- dashboard/http_app/post_routes.py.
- TTS: POST /api/tts (server-side edge-tts, en-GB-SoniaNeural by default) --
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
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

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
    def __init__(self, base_url: str = "http://localhost:8765") -> None:
        self._base_url = base_url.rstrip("/")

    def listen(self) -> str:
        """Record one silence-terminated utterance and return its cleaned transcript.

        Returns "" if nothing was said within MAX_WAIT_FOR_SPEECH_MS -- that is
        not an error, it just means the caller should listen again.
        """
        audio = self._record_until_silence()
        if audio is None:
            return ""

        request = urllib.request.Request(
            f"{self._base_url}/api/voice",
            data=audio,
            headers={"X-Filename": "audio.wav", "Content-Type": "audio/wav"},
            method="POST",
        )
        try:
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
            stderr=subprocess.DEVNULL,
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
        """Synthesize `text` via the dashboard's edge-tts endpoint and play it.
        Fails closed: a broken player/request only skips playback, never crashes
        the conversation -- the reply is always already printed to the console.
        """
        if not text.strip():
            return
        player = shutil.which("ffplay") or shutil.which("mpg123") or shutil.which("paplay")
        if not player:
            print("[voice output unavailable: none of ffplay/mpg123/paplay on PATH]")
            return

        request = urllib.request.Request(
            f"{self._base_url}/api/tts",
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "")
                payload = response.read()
        except (urllib.error.URLError, OSError) as exc:
            print(f"[voice output failed: {exc}]")
            return

        if "audio" not in content_type:
            error = json.loads(payload.decode()).get("error", "unknown TTS error")
            print(f"[voice output failed: {error}]")
            return

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(payload)
            mp3_path = f.name
        try:
            args = (
                [player, "-nodisp", "-autoexit", "-loglevel", "quiet", mp3_path]
                if "ffplay" in player
                else [player, mp3_path]
            )
            subprocess.run(args, capture_output=True)
        finally:
            Path(mp3_path).unlink(missing_ok=True)

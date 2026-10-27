# Receptionist latency and fallback

Toyota's live 2026-10-25 timing probes improved from 55–62 seconds per one-line
reply to 6.15, 3.50, and 2.55 seconds after these repairs. The latter two
requests encountered real Groq token limits and completed through OpenAI.

The main local defect was `latest_user_message()` using an unanchored,
backtracking regex over a prompt that no longer ended with `AGENT:`.
WorkflowModel appends operational instructions and a final JSON message
envelope. Extraction now reads that envelope with `rpartition` and JSON
validation, or uses bounded string operations for the legacy template. It
never treats recalled chat or capability examples as the current request.

## Provider policy

- `main.build_chat_model()` composes timed Groq and OpenAI adapters behind
  `FallbackModel`. Every Groq token-limit refusal hands off immediately.
- Both SDK clients use `max_retries=0`. There is no extra RetryingModel around
  these two providers. Groq has an 8 second HTTP timeout; OpenAI has 20 seconds.
- OpenAI uses the existing scoped key and configured fallback model. Missing
  credentials are reported at startup and keep the original Groq-only mode.
- OpenAI's reasoning-effort correction runs once only after an explicit
  unsupported-effort response. A failed corrected request reports that actual
  failure, rather than the older setting error.
- Streaming responses have a 30 second elapsed budget checked between chunks,
  in addition to the HTTP read timeout. A partial response is never replayed
  through a backup: doing so could repeat speech or mix two providers' replies.

The official SDK's retry and timeout behavior is documented in the
[OpenAI Python reference](https://developers.openai.com/api/reference/python).
The local policy explicitly overrides those defaults.

## Streaming contract

`GroqLanguageModel` and `OpenAILanguageModel` accept an optional text callback.
The shared chat-stream adapter consumes only public content, records usage,
checks the provider's terminal finish reason, and closes its response. REPL
callers without a callback retain the complete-reply interface.

`PublicReplyRelay` holds possible `ROUTE:`, `WORK:`, and `OPEN_AGENT:` prefixes
until the complete response is validated by the existing routing/workflow
decorators. Only ordinary text produces `TurnRecord(kind="delta")`. Blank
tokens are coalesced with the next word so browser blank-event filters do not
remove spaces. Each turn still persists its complete final reply normally.

The final `reply` snapshot has `streamed=True` if deltas were delivered. The
dashboard bridge displays the deltas and suppresses that redundant snapshot.
Acknowledgments, tool operations, handoffs, results, and errors keep their
existing semantics. Dropping a client still lets a delegated operation finish.

The dashboard speech queue prefetches later sentence audio while the current
sentence plays. Playback stays ordered; cancellation aborts outstanding audio
fetches and prevents stale playback. Edge TTS uses a shared per-cache-path lock
and unique temporary files so concurrent identical requests synthesize once.
Fresh first-sentence synthesis still depends on the external Edge TTS service.

## Operational timing

Content-free `[turn-timing]` JSON records in `/tmp/toyota_receptionist.log`
include a request ID, stage, duration, and outcome. Stages cover queue wait,
embedding, search/recency recall, workflow state/operations, each provider,
first public reply text, persistence, and the whole turn. Port decorators keep
instrumentation outside `ConversationAgent` and preserve its import boundary.

Run the focused offline suites from `lancedb_memory/`:

```bash
.venv/bin/python -m receptionist_team.tests.test_latest_user
.venv/bin/python -m receptionist_team.tests.test_provider_handoff
.venv/bin/python -m receptionist_team.tests.test_reply_stream
.venv/bin/python -m receptionist_team.tests.test_turn_telemetry
.venv/bin/python -m receptionist_team.tests.test_service_turns
```

Dashboard bridge tests are in `dashboard/tests/test_toyota_service.py`;
speech cache/concurrency tests are in `dashboard/tests/test_synthesis.py`;
browser speech tests are in `dashboard/js/tests/edge-tts-speech-synthesizer.test.js`
and `dashboard/js/implementation/conversation_coordinator/tests/`.

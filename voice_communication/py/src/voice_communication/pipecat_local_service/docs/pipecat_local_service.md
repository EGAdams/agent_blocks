---
id: pipecat-local-service
name: PipecatLocalService
group: Planned core
status: partial
---

# PipecatLocalService

> The first concrete class in this skeleton's Python tree. Owns session bookkeeping only — no real audio, transcription, or synthesis is wired in yet.

**Partial** — `PipecatLocalServiceFacade` satisfies the `PipecatLocalService` Protocol and is exercised by 5 tests, but it depends only on `PipelineFactory`; `AudioCapture`, `TranscriptionStrategy`, and `SpeechSynthesizer` have no adapter to inject, real or fake, so no audio ever actually flows.

## Responsibility

- This is what `IPipecatServiceClient` on the TS side talks to. It is the boundary that keeps Pipecat itself out of every policy object: nothing upstream imports Pipecat, whisper, or a TTS SDK directly.
- Owns exactly one thing: the `session_id -> PipelineHandle` map, built through the injected `PipelineFactory` at `start_session()` and torn down at `stop_session()`. It does not itself do turn detection, transcription, or synthesis — those are `PipelineFactory`'s dependencies to assemble into the pipeline it hands back.
- `health()` is what makes this object observable from the other process without either side reaching into the other's internals; per `docs/build_plan.md` step 6, this is the one method this repository requires to work.

## Contract

```python
PipecatLocalService(Protocol)   pipecat_local_service.py

  start_session(request) -> StartSessionResponse   mints a session_id, builds its pipeline
  submit_audio(frame)    -> None                    raises KeyError for an unknown session_id
  stop_session(request)  -> None                     idempotent
  health()               -> VoiceHealthEvent
```

## Implementations

- **PipecatLocalServiceFacade** (current) — `pipecat_local_service.py`
  The Facade GoF role named for this port in the root `CLAUDE.md`. Constructor-injected with one `PipelineFactory`; holds no reference to Pipecat, a transport, or a DOM API.

## Tests

- `py/tests/pipecat_local_service/test_pipecat_local_service.py` — 5 tests
  `health()` round-trips through pydantic's camelCase serialization (`latencyMs`, not `latency_ms`) — the exact property step 6's DONE WHEN checks for. `start_session()` mints a fresh `UUID`. `submit_audio()` raises `KeyError` for a session that was never started and is silent for one that was. `stop_session()` is idempotent for an unknown session.

### Not covered

- Anything involving real audio: no `AudioCapture`, `TranscriptionStrategy`, or `SpeechSynthesizer` adapter exists yet, fake or real, so no pipeline this class builds ever processes a frame.
- Concurrent sessions racing on the same `session_id` — the map is not synchronized, and nothing in this codebase is concurrent yet either.

## Development status

### Done

- Satisfies the `PipecatLocalService` Protocol structurally (Python `Protocol`s are duck-typed; no explicit inheritance needed).
- `health()` proves the wire format round-trips exactly as `contracts/test_contract_parity.py` expects, before any other Python block does.

### Gaps

- No real `PipelineFactory` exists — only the test's `FakePipelineFactory`. `PipelineFactory.create()`'s real implementation is what step 6's own text calls out as "gets a real adapter after it."
- No server process serves this over HTTP yet; that is step 7's `IPipecatServiceClient` wire, which this object's `health()` output now feeds via `contracts/fixtures/voice_health_event.json`.

## Dependencies

### Depends on

- `PipelineFactory` — injected; this facade is untestable without a fake specifically because the dependency is inverted correctly.

### Used by

- Nothing yet. `IPipecatServiceClient` (TS) is this port's future caller once a real HTTP entry point wraps it.

## Next work

- Build a real `PipelineFactory` adapter and let `start_session()` actually assemble `AudioCapture` + `TranscriptionStrategy` + `SpeechSynthesizer` into something that moves a frame.
- Put this facade behind an actual HTTP server process, so `IPipecatServiceClient` has something live to call instead of a fixture.

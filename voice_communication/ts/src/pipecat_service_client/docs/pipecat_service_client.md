---
id: pipecat-service-client
name: IPipecatServiceClient
group: Planned core
status: partial
---

# IPipecatServiceClient

> The first port with a real caller on both sides of the wire, but no live server yet — the "both sides accept the same fixture" bar from `docs/build_plan.md` step 7.

**Partial** — `HttpPipecatServiceClient` satisfies the port and is tested against a recorded JSON fixture; nothing serves that fixture over a real socket yet.

## Responsibility

- The Adapter role named for this port in the root `CLAUDE.md`: the only object on the TS side allowed to know the local Pipecat service is reachable over HTTP. Everything upstream (`ToyotaVoiceApplication`, `ConversationCoordinator`) would depend on `IPipecatServiceClient`, never on `fetch` or a URL directly.
- Mirrors `PipecatLocalService` (Python) method-for-method — `startSession`/`sendAudio`/`stopSession`/`health` — because it is the same four operations seen from the other process.

## Contract

```ts
IPipecatServiceClient   pipecat_service_client.ts

  startSession(request: StartSessionRequest) -> Promise<StartSessionResponse>
  sendAudio(frame: AudioFrame)               -> Promise<void>
  stopSession(request: StopSessionRequest)   -> Promise<void>
  health()                                    -> Promise<VoiceHealthEvent>
```

## Implementations

- **HttpPipecatServiceClient** (current) — `http_pipecat_service_client.ts`
  `fetch` is constructor-injected (same pattern as `LettaAgentAdapter`), so every test stubs it rather than hitting a socket. Registered in `design_protocol.ts`'s `KNOWN_TRANSPORT_ADAPTERS`, since it is one of only two files in this tree allowed to reference `fetch`/`AbortController`.
- **parseVoiceHealthEvent** (current) — `http_pipecat_service_client.ts`
  Fails closed on a malformed or unrecognized `status` rather than guessing — the same shape of guarantee `LettaAgentAdapter`'s response parsing makes.

## The shared wire fixture

`contracts/fixtures/voice_health_event.json` is one recorded `VoiceHealthEvent`, camelCase. It is what makes step 7's DONE WHEN checkable without a running Python process:

- `pipecat_service_client.test.ts` reads it and asserts `parseVoiceHealthEvent` accepts it, then again through `HttpPipecatServiceClient.health()` with `fetch` stubbed to return it.
- `py/tests/pydantic_models/test_pydantic_models.py` reads the identical file and asserts pydantic's `VoiceHealthEvent.model_validate_json` accepts it and round-trips to the same camelCase JSON.

Both sides parsing the same bytes is the actual test of the contract — this is not "the two catalogs agree on paper" (`contracts/test_contract_parity.py`'s job), it is "the two runtimes agree on one real payload."

## Tests

- `ts/src/pipecat_service_client/pipecat_service_client.test.ts` — 3 tests
  The fixture parses; the full adapter path (stubbed `fetch` → `health()`) parses it too; an unrecognized `status` throws rather than being silently accepted.
- `py/tests/pydantic_models/test_pydantic_models.py` — 1 test
  The same fixture round-trips through pydantic's alias-based camelCase serialization.

### Not covered

- A real HTTP round trip — no server process exists yet to bind `PipecatLocalServiceFacade` to a port.
- `startSession` / `sendAudio` / `stopSession` have no dedicated tests beyond typechecking against the port; only `health()` has a fixture to check against, because that is the one method step 6 required to work.

## Development status

### Done

- The port has a real adapter, constructor-injected for testability, holding no other public surface than `IPipecatServiceClient` plus nothing extra (unlike `LettaAgentAdapter`, this one needed no `cancel()` — Pipecat is a different failure shape than a single-shot HTTP agent call).
- The wire is checked with the same bytes on both sides, not just the same field names.

### Gaps

- No server. `PipecatLocalServiceFacade` (Python) is never actually reachable at `baseUrl` yet.
- No caller. `ToyotaVoiceApplication` does not hold an `IPipecatServiceClient` — today's `Speaker` callback in that facade is a placeholder for what would eventually be `client.sendAudio`-driven playback or a `SpeakRequest` over this same client.

## Dependencies

### Depends on

- `fetch` — the one transport this file is allowed to import, per `design_protocol.ts`.

### Used by

- Nobody yet.

## Next work

- Stand up an actual HTTP entry point over `PipecatLocalServiceFacade` so this adapter has a real socket to hit, then add an integration test gated behind that server actually running.
- Give `ToyotaVoiceApplication` (or `ConversationCoordinator`) a real `IPipecatServiceClient` dependency once there is audio to send, replacing the placeholder `Speaker` callback.

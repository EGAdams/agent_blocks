# Recent Activity

> Log of chunks of work completed against `build_plan.md`, so a session can
> pick up the next nugget without re-reading the whole plan. Newest first.

## 2026-09-07 — Steps 6-7 plus two unordered chunks: Python activated, the wire checked, a fitness test, ConversationCoordinator

Five more chunks, crossing into the Python side and the "Not in the order"
section for the first time. Note: `build_plan.md`'s "Scope" section flags
that step 6 onward commits to the Pipecat-rebuild direction over the
shipped-dashboard-stack direction — that open question is still unresolved;
this work proceeded anyway on explicit instruction.

- **Step 6** — `pipecat_local_service/pipecat_local_service.py` gained
  `PipecatLocalServiceFacade`, the first concrete class anywhere in `py/`.
  Constructor-injected with one `PipelineFactory`; owns only the
  `session_id -> PipelineHandle` map. `health()` returns a `VoiceHealthEvent`
  that round-trips through pydantic's camelCase serialization — the step's
  literal DONE WHEN. 5 tests in the now-no-longer-empty `py/tests/`, run
  manually (no `pytest` install reachable from this sandbox; verified by
  importing the test module and calling each `test_*` function directly —
  see the Running section for the substitute invocation). Wrote
  `pipecat_local_service/docs/pipecat_local_service.md` in the same edit —
  one of the six blocks `build_plan.md` calls out as having no doc yet.
- **Step 7** — `pipecat_service_client/http_pipecat_service_client.ts`
  gained `HttpPipecatServiceClient` (constructor-injected `fetch`, same
  pattern as `LettaAgentAdapter`) and `parseVoiceHealthEvent` (fails closed
  on an unrecognized `status`). Added `contracts/fixtures/voice_health_event.json`
  — one recorded `VoiceHealthEvent`, camelCase — read by both
  `pipecat_service_client.test.ts` (TS) and
  `py/tests/pydantic_models/test_pydantic_models.py` (Python), each
  asserting its own runtime accepts the identical bytes. This is a stronger
  check than `contracts/test_contract_parity.py`: that test compares the two
  catalogs' shapes on paper, this one proves two real parsers agree on one
  real payload. Wrote `pipecat_service_client/docs/pipecat_service_client.md`.
- **`IVoiceDesignRule`** (unordered, "useless before step 2" — now well past
  it with two transport-touching adapters) — `design_protocol.ts` gained
  `VoiceDesignRule`, backed by an explicit `KNOWN_TRANSPORT_ADAPTERS`
  registry (`letta_agent_adapter`, `http_pipecat_service_client`) rather
  than a filename-suffix inference, so granting a new adapter the exception
  is a visible, reviewable edit to this one list. `design_protocol.test.ts`
  both unit-tests the rule directly and runs it as a real fitness test:
  scans every non-test `.ts` file under `src/` for `fetch`/`AbortController`
  references and asserts each hit's module is in the registry. (Existing
  `design_protocol.md` was left untouched — like the other pre-existing
  per-block docs, it describes the shipped dashboard's SOLID/GoF working
  agreement, not this skeleton.)
- **`ConversationCoordinator`** (deliberately unordered, and deliberately
  last of the "planned core" ports) — `conversation_coordinator.ts` gained
  the concrete `ConversationCoordinator`: the Mediator between one
  *finalized* `TranscriptEvent` (interim ones are ignored) and one agent
  run. Its fence check happens **after** awaiting the agent's full event
  stream, not just before — `session.accepts(generation)` is re-checked
  right before speaking, so an `interrupt()` landing mid-turn still
  suppresses a reply that already finished generating. This was a real bug
  fix, not new scope: `ToyotaVoiceApplication`'s step-5 `runTurn()` never
  re-checked the fence after the agent responded, so an interrupt mid-flight
  would have been spoken anyway. **Refactored** `ToyotaVoiceApplication` to
  build a `ConversationCoordinator` from its four dependencies and delegate
  to it, rather than duplicating the turn loop — it now only composes.
  Wrote `toyota_voice_application/docs/toyota_voice_application.md` (missed
  in the same edit last session when step 5 first activated it; caught and
  fixed now). `conversation_coordinator.md` (pre-existing, dashboard-focused)
  left untouched, same reasoning as `design_protocol.md` above.

New ambient shims added along the way, extending the pattern from Step 0:
`node:fs/promises`, `node:url`, and `process.cwd()` (in
`pipecat_service_client/node_fs_shims.d.ts`), plus `assert.notEqual` added
to the existing `node_test_shims.d.ts`. Confirmed during this work that
TypeScript's implicit default lib already includes DOM (hence `fetch` and
`AbortController` needed no shim, unlike `node:*` modules) — worth knowing
before adding another ambient global shim reflexively.

One test-runner gotcha hit and fixed: `design_protocol.test.ts`'s fitness
scan initially computed its source-file root from `import.meta.url`, which
resolves to the *compiled* `dist-test/` location at runtime — scanning
compiled `.js` for files ending in `.ts` silently found nothing. Fixed by
deriving the root from `process.cwd()` instead (stable at `ts/dist-test`
because `npm test`'s script `cd`s there before running).

Verified: `npm test` (22 TS tests) and `npm run typecheck` both exit 0 from
`ts/`; 6 Python tests pass (run manually per above); `contracts/test_contract_parity.py`
still reports "contract parity OK" (untouched — no contract shapes changed).

**Next nugget:** Step 6's own Gaps section names the real follow-on: a real
`PipelineFactory` adapter, so `PipecatLocalServiceFacade.start_session()`
assembles actual `AudioCapture` + `TranscriptionStrategy` + `SpeechSynthesizer`
instead of a bare pipeline handle from a fake. After that, `input_draft` and
`voice_health_observer` are the two remaining undocumented-and-unactivated
blocks; `IPipecatServiceClient` still has no real server to call.

## 2026-09-07 — Step 0: TypeScript test runner wired up

`ts/` had no test runner and no `node_modules/`. Added, zero-dependency:

- `ts/tsconfig.test.json` — extends `tsconfig.json`, flips `noEmit` off,
  emits to `ts/dist-test/` (gitignored).
- `ts/src/node_test_shims.d.ts` — minimal ambient `declare module` for
  `node:test` / `node:assert/strict`, since no `@types/node` is vendored.
- `ts/src/sanity.test.ts` — trivial passing `node:test` test.
- `ts/package.json` — `"test": "tsc -p tsconfig.test.json && node --test dist-test"`.
- `ts/.gitignore` — `node_modules/`, `dist-test/`.

Verified: `npm test` and `npm run typecheck` both exit 0 from `ts/`.

## 2026-09-07 — Style: bracket-spacing convention adopted for this directory

Per user instruction, `voice_communication/CLAUDE.md`'s Style section now
requires the root CLAUDE.md's bracket-spacing rule here too (it previously
opted out explicitly). Applied to the files from Step 0: single-line
brackets/parens/braces get inner padding (`test( "x", () => {`,
`assert.equal( 1 + 1, 2 )`); multi-line ones don't force a space where the
bracket sits alone on its own line, matching `lancedb_memory/agent_memory/`.
JSON config files (`package.json`, `tsconfig*.json`) were left in standard
JSON formatting — the convention's precedent is source files, not configs.
Apply this convention to every file added here going forward.

## 2026-09-07 — Steps 1-5: the TS side end to end, agent to speaker

Knocked out five build-plan chunks in one pass, all under the new
bracket-spacing convention:

- **Step 1** — `conversation_agent/fake_conversation_agent.ts`
  (`FakeConversationAgent`, scriptable) + `conversation_agent.contract.ts`
  (the shared Liskov suite, exported so step 2 could reuse it, not just a
  `.test.ts`) + `conversation_agent.test.ts` running it against the fake.
- **Step 2** — `letta_agent_adapter/letta_agent_adapter.ts`: `submit()` now
  POSTs `{ text }`, gets back `{ ok, reply }`, yields `assistant_text` then
  `terminal`, or `error` on `ok: false`. 900s timeout via `AbortController`
  (TS's default lib already carries DOM types — `fetch`/`AbortController`
  needed no ambient shim, unlike `node:test`). `fetch` is constructor-injected
  (`fetchImpl`) for testability rather than monkey-patched globally. Added
  `cancel(generation)` — not on `IConversationAgent`, adapter-only — which
  suppresses delivery without stopping the run, per `overview.md`. Reuses the
  step-1 contract suite plus two adapter-specific tests (error mapping,
  cancel-before-delivery).
- **Step 3** — `voice_session/voice_session.ts` gained the concrete
  `VoiceSession` class in the same file as `IVoiceSession` (single canonical
  implementation, not a swappable Strategy, so no separate adapter file).
  `beginTurn()` increments a generation counter from a `0` = "no live
  generation" sentinel; `interrupt()`/`close()` reset to it so `accepts()`
  fails closed.
- **Step 4** — `spoken_output_policy/spoken_output_policy.ts` gained
  `SpokenOutputPolicy`, same same-file reasoning as step 3. Filters to
  `generation === current && kind is in SPEAKABLE_KINDS` (`assistant_text`
  only).
- **Step 5** — `toyota_voice_application/toyota_voice_application.ts` gained
  `ToyotaVoiceApplication`, the composition root, plus a small `Speaker`
  type (`(text: string) => void`) — not a new port in `interfaces/index.ts`,
  since real speech output is Python/Pipecat's job later (step 6+) and this
  is just a test/caller-supplied sink for now. `runTurn()` is the one method
  naming `IConversationAgent`/`IVoiceSession`/`ISpokenOutputPolicy` together.

Concrete classes were deliberately **not** added to
`ts/src/interfaces/index.ts` — that catalog only re-exports types/ports, per
CLAUDE.md ("concrete types get selected at a composition root, not in a
catalog").

Fixed along the way: `node --test dist-test` (a bare directory argument)
only ran one aggregate subtest instead of recursing into per-block
subdirectories. `ts/package.json`'s `test` script now does
`tsc -p tsconfig.test.json && cd dist-test && node --test` so it discovers
all `*.test.js` files recursively.

Did **not** touch `ts/src/*/docs/*.md` for the activated blocks — those
docs describe the already-shipped `letta-code/dashboard` JS implementation
(a different codebase), not this skeleton; see `build_plan.md`'s "Scope"
section. Updating this repo's own activation status lives here instead.

Verified: `npm test` (15 tests, all passing) and `npm run typecheck` both
exit 0 from `ts/`; `contracts/test_contract_parity.py` still reports
"contract parity OK" (untouched — no contract shapes changed).

**Next nugget:** Step 6 — the Python side, `PipecatLocalService`, per
`build_plan.md` § 6 — but read the "Scope" section first: steps 1-5 were
worth doing either way, step 6 onward commits to the Pipecat-rebuild
direction over the shipped-dashboard-stack direction, and that question is
still open.

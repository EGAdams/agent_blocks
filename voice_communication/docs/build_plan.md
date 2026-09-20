---
id: build_plan
name: Build Plan
group: Start here
status: partial
---

# Build Plan

> The construction order for this skeleton. Read `overview.md` first for what
> the system is; this file is only *what to build next, and how you know it works*.

**Partial** — Nothing in this repository is activated yet. Every block is a
port; the one class (`LettaAgentAdapter`) throws. Step 0 below is a
prerequisite for verifying any of the rest.

## Scope: this repository, not the dashboard

`overview.md` § Next work is a six-step **adoption backlog for the live
dashboard** at `/home/adamsl/letta-code/dashboard`. Those steps edit renderers
in `dashboard/js/`; not one of them touches a file in this tree. It is a real
plan, but it is not this repository's plan, and following it here would produce
nothing.

This file is the construction order for *these* ports. The two plans meet at
the open question `overview.md` closes with — whether the Pipecat rebuild is
still the direction, or whether the shipped dashboard stack is now the system.
Until that is answered, work here is a parallel implementation of behavior that
already runs elsewhere. **Answer it before step 1**; steps 1-3 are worth doing
either way, since they are the objects the dashboard already proved out, but
step 5 onward commits to the Pipecat direction.

## Rules that constrain every step

Restated from `CLAUDE.md` because they decide where files go:

- An adapter lives **in its port's own directory**, never in a shared bucket.
- The adapter is the only file allowed to import an SDK, a transport, or a DOM
  API. Everything else depends on the port.
- Concrete types are selected at a composition root, not in a catalog.
- Change a shape on one side, change it on the other in the same edit, then run
  `contracts/test_contract_parity.py`.
- TS tests colocate (`<block>.test.ts`); Python tests mirror into `py/tests/`.

## Order

### 0 · Wire a TypeScript test runner

Nothing below can be verified without one, and `ts/` has no runner and no
`node_modules/`. Use `node:test` + `node:assert/strict` — zero dependencies,
which keeps the "nothing vendored" property. Add a `test` script to
`ts/package.json` that typechecks and then runs the compiled tests.

**DONE WHEN:** `cd ts && npm test` runs a trivial passing test file and exits 0.

### 1 · `FakeConversationAgent`, beside `IConversationAgent`

Build the fake *before* the real adapter. It is what every later step tests
against, it needs no network, and it forces the port's semantics to be stated
in code: events carry the generation they were submitted with, and the stream
terminates exactly once.

`ts/src/conversation_agent/fake_conversation_agent.ts` +
`conversation_agent.test.ts`.

**DONE WHEN:** a contract test drives the fake through a full turn and asserts
the last event's `kind` is `"terminal"` or `"error"`, never both and never
neither.

### 2 · `LettaAgentAdapter.submit()`

Replace the throw. It POSTs `/api/letta-code-message` and gets back one lump —
`{ ok, reply }` — so the async iterable yields a single `assistant_text` event
then a `terminal`, with `ok: false` becoming `error`. `fetch` appears here and
nowhere else in the tree.

Two things `overview.md` records that the code must honor: the 900s ceiling is
the adapter's own timeout, and `cancel()` means *do not deliver*, not *stop the
run* — do not name it in a way that promises otherwise.

**DONE WHEN:** the same contract suite from step 1 passes against the adapter
with `fetch` stubbed, and a superseded generation's response is not yielded.

### 3 · `VoiceSession`, the fence

`ts/src/voice_session/voice_session.ts` gets a concrete class beside it.
`beginTurn()` mints a monotonically increasing `GenerationId`; `accepts()` is
true only for the current one; `interrupt()` advances the generation so
everything in flight is orphaned.

**DONE WHEN:** a test submits turn A, interrupts, submits turn B, and asserts
A's late events are all rejected by `accepts()`.

### 4 · `SpokenOutputPolicy`, the gate

The single decision about what reaches a speaker: filter to the current
generation, then to speakable kinds (`assistant_text`; not `reasoning`, not
`tool`). This is where a regex-over-the-reply-text approach gets replaced by
the event's own `kind`.

**DONE WHEN:** a test asserts an `assistant_text` from a superseded generation
is dropped, and a `reasoning` event from the *current* generation is dropped too.

### 5 · The TS composition root

`IToyotaVoiceApplication` is the facade. Its `start()` is the first and only
place naming `LettaAgentAdapter`, `VoiceSession`, and `SpokenOutputPolicy`
together. Until this exists, steps 1-4 have the same zero-callers problem the
dashboard objects have.

**DONE WHEN:** a test constructs the application with the *fake* agent, runs a
turn end to end, and asserts exactly the expected text reaches a fake speaker.

### 6 · The Python side: `PipecatLocalService`

Only now, and only if the Pipecat direction survived the question above. The
service facade comes first because it is what `IPipecatServiceClient` talks to;
`PipelineFactory`, `AudioCapture`, `TranscriptionStrategy`, and
`SpeechSynthesizer` are its dependencies and get real adapters after it, each
with a mirrored test in `py/tests/<block>/`.

**DONE WHEN:** `py/tests/` is no longer empty and the service answers
`health()` with a `VoiceHealthEvent` that round-trips through the parity test's
camelCase serialization.

### 7 · `IPipecatServiceClient` and the wire

The first point where both processes exist at once, and therefore the first
real test of the contract. Everything before this is single-process.

**DONE WHEN:** a TS test and a Python test exchange the same recorded JSON
fixture and both accept it.

## Not in the order

- **`ConversationCoordinator`** — deliberately last, and not numbered.
  `overview.md` is right that its job only becomes visible once the steps above
  are real code. Do not design it from the plan.
- **`IVoiceDesignRule` / `design_protocol`** — the fitness test over the
  "adapters import SDKs, ports don't" rule. Worth activating once there are
  enough adapters for it to have something to check; useless before step 2.
- **The six undocumented blocks** — `input_draft`, `pipecat_service_client`,
  `toyota_voice_application`, `voice_health_observer`, `pipecat_local_service`,
  `pipeline_factory` have ports but no `docs/<block>.md`, because the original
  spec set never covered them. Write each one's doc in the same edit that
  activates it, rather than as a documentation pass.

## Links

- [Overview](overview.md) — what exists, and the dashboard adoption backlog
- [Contract parity](../contracts/README.md) — what the cross-process test compares

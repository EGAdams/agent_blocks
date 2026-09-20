# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

`voice_communication/` is a **contracts-only skeleton**, not a running program.
Every file here is either a type, an interface/Protocol, a catalog that
re-exports them, or the parity test over the two catalogs. The single piece of
executable production code is `ts/src/letta_agent_adapter/letta_agent_adapter.ts`,
and its one method throws `"not implemented yet"` on purpose.

Read `docs/overview.md` first — it is the specification for the system as a
whole, and its opening gotcha ("the objects are built, nothing calls them,
both halves are true") is the single most important thing to understand before
implementing. Then `docs/build_plan.md` for the construction order — and note its opening
section, because `overview.md`'s "Next work" backlog targets the live
dashboard, not this tree. Then `README.md` for the layout, and the root
`/home/adamsl/agent_blocks/CLAUDE.md` for workspace-wide context.

Consequence for any task here: adding behavior means *activating a block*
(adding a concrete adapter + tests beside its interface), not editing the
interface catalogs. Do not put implementations in `interfaces/`,
`typescript_contracts/`, or `pydantic_models/`.

## Two package roots, one repository

The system spans two processes, so the tree splits at the **package root**:

- `ts/` — the Toyota dashboard / browser application. Its own `package.json`
  and `tsconfig.json`; sources under `ts/src/`.
- `py/` — the separate local Pipecat service. Its own `pyproject.toml`;
  a src layout, so the importable package is `py/src/voice_communication/`.
- `contracts/` — the only thing that spans both.

The two sides share no code and never import each other; the only linkage is
the JSON wire format. They are nonetheless one repository because they must
change in lockstep, and only a checkout containing both trees can enforce that.
**Do not "finish the split" by extracting either root into its own repo** —
that trades the one guarantee this skeleton exists to hold for nothing. Revisit
only if the Pipecat service acquires its own release cadence or a separate
owner; `git subtree split` over a clean `py/` handles it if that day comes.

## Commands

```bash
# TypeScript typecheck (the only real "build"; noEmit, strict)
cd voice_communication/ts && npm run typecheck        # or: npx tsc -p tsconfig.json

# Python import-check. There is no venv here — borrow the one with pydantic.
# The src layout means PYTHONPATH=src, from any cwd (no workspace-root trick).
cd voice_communication/py
PYTHONPATH=src ../../lancedb_memory/.venv/bin/python -c "import voice_communication as v; print(v.__all__)"

# Contract parity. No network, no API key. Standalone because the borrowed
# venv has pydantic but not pytest; the checks are plain test_* functions, so
# `python -m pytest contracts/` also works where pytest is installed.
cd voice_communication
../lancedb_memory/.venv/bin/python contracts/test_contract_parity.py
```

`py/pyproject.toml` declares `pytest` with `testpaths = ["tests"]` and
`pythonpath = ["src"]`; `py/tests/` is empty until the first activated Python
block fills it. `pyright` is configured `strict` over `py/src` and `py/tests`.

There is no `node_modules/` checked in; `npx tsc` resolves a system TypeScript.

## The two-catalog architecture

The central idea is **one boundary, expressed twice**, because the system spans
two processes:

- **TypeScript side** = Toyota's dashboard / browser application. Shapes in
  `ts/src/typescript_contracts/contracts.ts`, ports one-per-directory under
  `ts/src/`, all re-exported from `ts/src/interfaces/index.ts`.
- **Python side** = the separate local Pipecat service. Shapes in
  `py/src/voice_communication/pydantic_models/models.py`, `typing.Protocol`
  ports one-per-directory, all re-exported from
  `py/src/voice_communication/interfaces/__init__.py`.

The two catalogs deliberately mirror each other: same shape names, same field
names, same string values for `AgentEventKind` and `VoiceHealthStatus`.
`VoiceModel` (the pydantic base) sets `alias_generator=to_camel` plus
`serialize_by_alias=True` precisely so `latency_ms` serializes as `latencyMs`
to match the TS `latencyMs`. **When you change a shape on one side, change it
on the other in the same edit.** `contracts/test_contract_parity.py` now
enforces exactly that — run it after touching either catalog, and read
`contracts/README.md` for what it compares.

Known intentional-looking asymmetries, so you don't "fix" them blindly: TS uses
`SessionId = string` and `PipelineHandle.pipelineId: string` where pydantic uses
`UUID`. That is the JSON-over-the-wire representation vs the validated runtime
type. The parity test skips field *types* for this reason and lists these pairs
in `DOCUMENTED_TYPE_DIVERGENCES`; if you legitimately add another, add it there
with the reason, rather than loosening a check.

`VoiceModel` is fail-closed — `extra="forbid"`, `frozen=True`. New cross-process
shapes must inherit from it rather than `BaseModel` directly.

## Dependency direction

The whole point of the layout is that dependencies point *inward* to these
ports and never outward:

- No file here imports `pipecat`, `letta`, an STT/TTS SDK, a transport, or a
  DOM API. Concrete adapters will, and they will live beside their interface,
  never inside a catalog.
- Concrete types get selected at a future composition root — the Toyota
  dashboard on the TS side, a service entry point on the Python side.
- Every interface directory holds exactly one interface file named after the
  directory (`ts/src/voice_session/voice_session.ts`,
  `py/src/voice_communication/audio_capture/audio_capture.py`). Keep that shape
  when adding blocks; the catalogs' import paths assume it. A new block goes
  under the `src/` of the side that owns it — never at the root, and never
  under the other language's root.
- **Group by block, never by kind.** There is deliberately no top-level
  `concrete/`, `adapters/`, or `impl/` bucket, and adding one is a regression:
  the "only an adapter beside its port may import `pipecat`/`letta`/an SDK/a
  DOM API" rule is enforceable precisely because the adapter's location is
  predictable from the port's. An adapter goes in the port's own directory.
- `ts/src/design_protocol/design_protocol.ts` (`IVoiceDesignRule`) is the
  placeholder for an architectural fitness test over exactly this rule.

The GoF roles are assigned, and each port's docstring states which: Strategy
(`TranscriptionStrategy`, `SpeechSynthesizer`, `DetectionInterface`,
`ISpokenOutputPolicy`, `IRouteStrategy`, `ILanguageProcessor`), Factory Method
(`PipelineFactory`), Facade (`PipecatLocalService`, `IToyotaVoiceApplication`),
Observer (`IVoiceHealthObserver`), Adapter (`IPipecatServiceClient`,
`LettaAgentAdapter`).

## Where documentation and tests go

- **Docs live beside the port**: `<block>/docs/<block>.md`, with the system-wide
  `docs/overview.md` at the root. Front-matter is `id` / `name` / `group` /
  `status`; `id` is the documentation SPA's route key and must not be renamed
  to match the directory (`conversation_agent/docs/conversation_agent.md` keeps
  `id: iconversationagent`). Update the doc in the same edit as the port —
  `status` and `statusNote` are cross-checked by
  `spa_documentation/check_construction_status_consistency.py`.
- **These Markdown files are now the source of truth.** They were converted
  from `letta-code/dashboard/js/plans/voice-communication/specs/*.js`, which are
  now stale duplicates. Do not edit those; do not vendor the generated SPA back
  into this repo.
- **Python tests mirror, they do not colocate**: a block at
  `py/src/voice_communication/<block>/<block>.py` is tested at
  `py/tests/<block>/test_<block>.py`. This is the one deliberate break from
  beside-its-interface — a src layout would otherwise ship tests in the wheel.
- **TypeScript tests colocate**: `<block>.test.ts` beside the port. `ts/` has no
  test runner wired up yet; `node:test` is the zero-dependency default if one is
  needed, since no `node_modules/` is checked in.
- Anything outside this directory that resolves paths into it —
  `spa_documentation/doc_source_map.py` holds a table of all 22 — is invisible
  to this repo's tooling. Grep for `voice_communication/` there after moving
  any file.

## Semantics already encoded in the contracts

These are the design decisions the skeleton exists to hold; preserve them.

- **Generation fencing.** `GenerationId` threads through `TranscriptEvent`,
  `AgentEvent`, and `SpeakRequest`. `IVoiceSession.beginTurn()` mints one and
  `accepts(generation)` is the fence — late events from a superseded turn must
  be dropped, not spoken. `ISpokenOutputPolicy.select()` takes the current
  generation for the same reason.
- **`IInputDraft` is not a DOM wrapper.** Its docstring is load-bearing:
  clearing only the visible control leaves a transcript accumulator holding the
  old committed prefix, which the next recognition callback restores. Any
  concrete adapter must clear both.
- **`IConversationAgent.submit()` returns `AsyncIterable<AgentEvent>`**, the
  same port shape `claude_agent_adapter/` already implements in Python.
  `LettaAgentAdapter` is the sibling role for Letta and is the next thing to
  implement — see `docs/build_plan.md` for the construction order.
- **Pipecat is a separate local process.** TS talks to it only through
  `IPipecatServiceClient`; the service exposes only `PipecatLocalService`.

## Style

Like `lancedb_memory/agent_memory/` and `catherine_agent_sdk/`, files here
enforce the root CLAUDE.md's bracket-spacing rule: every non-empty `)`, `]`,
or `}` gets a space before it — `str )`, `[ 0.0 ]`, `{ "a": 1 }` — while an
empty pair stays tight (`()`, `[]`, `{}`). This applies to every bracket
type, not just call parens. The space belongs only next to the innermost
non-bracket content: when closing brackets are adjacent, they stack with no
space between them — `] )` becomes `])`, and `] ) )` becomes `]))`, however
deep the nesting.

The same rule mirrors on the opening side: every non-empty `(`, `[`, or `{`
gets a space after it, next to the innermost non-bracket content, and
adjacent opening brackets stack tight with no space between them.

Write closing and opening brackets this way from the start rather than
relying on a cleanup pass afterward.

Every module and port carries a one-line docstring/JSDoc explaining its *role in
the boundary*, not its mechanics. Keep that when adding files.

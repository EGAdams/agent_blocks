# Voice Communication

This directory is the source-side skeleton for the Voice Communication Agent
Blocks. It establishes types and dependency boundaries only; production
behavior will be added one active block at a time.

## Layout

The system spans two processes, so it has two package roots and one shared
contract. Each root installs, typechecks, and tests independently; neither
imports the other.

```text
voice_communication/
  docs/overview.md            start here: what is built, what is wired
  ts/                         Toyota / browser / dashboard side
    package.json tsconfig.json
    src/
      interfaces/index.ts     public TypeScript interface catalog
      typescript_contracts/   browser boundary shapes
      voice_session/          one directory per individual Agent Block
        voice_session.ts        the port
        docs/voice_session.md   the block's specification
        (adapters + <block>.test.ts land here when the block activates)
      conversation_agent/
      ...
      index.ts                thin TypeScript package facade
  py/                         the separate local Pipecat service
    pyproject.toml
    src/voice_communication/
      interfaces/__init__.py  public Python interface catalog
      pydantic_models/        cross-process runtime validation shapes
      audio_capture/          one directory per individual Agent Block
        audio_capture.py        the port
        docs/audio_capture.md   the block's specification
      pipecat_local_service/
      ...
      __init__.py             thin Python package facade
    tests/                    mirrors src/: tests/<block>/test_<block>.py
  contracts/                  the parity spec and the test that enforces it
```

Within each root the original rule holds unchanged: an object's interface stays
in its own directory, and its concrete adapter and specification live beside
that interface. Concrete dependencies are selected at a future composition
root, never inside the interface catalog.

Blocks are grouped by *block*, never by kind — there is deliberately no
top-level `concrete/` or `adapters/` bucket. Grouping implementations together
would break the one rule this layout exists to make greppable: nothing imports
`pipecat`, `letta`, an STT/TTS SDK, or a DOM API except an adapter sitting next
to the port it implements.

Tests are the one exception, and only on the Python side: a src layout would
ship colocated tests inside the wheel, so `py/tests/` mirrors the package tree
instead. TypeScript colocates `<block>.test.ts` beside the port.

The split is at the package root, not at the repository. The two sides share no
code and never import each other — the only linkage is the JSON wire format —
but they must change in lockstep, which is exactly what `contracts/` exists to
enforce and what two repositories would make unenforceable.

## Commands

```bash
cd voice_communication/ts && npm run typecheck    # or: npx tsc -p tsconfig.json

# There is no venv here; borrow the one that has pydantic.
cd voice_communication/py
PYTHONPATH=src ../../lancedb_memory/.venv/bin/python -c "import voice_communication as v; print(v.__all__)"

cd voice_communication
../lancedb_memory/.venv/bin/python contracts/test_contract_parity.py
```

`py/tests/` is empty; the first activated Python block fills it. There is no
`node_modules/` checked in — `npx tsc` resolves a system TypeScript.

## Type boundaries

- `ts/src/interfaces/index.ts` is the public TypeScript interface catalog.
- `py/src/voice_communication/interfaces/__init__.py` is the public Python one.
- TypeScript interfaces describe Toyota/browser/application objects.
- Pydantic models validate every JSON shape crossing into or out of the
  separate local Pipecat service.
- Python Protocols define the Pipecat-side Strategy, Factory Method, and Facade
  ports.
- Concrete Letta, Pipecat, STT, TTS, transport, and logging adapters will depend
  on these ports rather than the reverse.

The TypeScript and pydantic contracts intentionally share field names and event
values. `contracts/test_contract_parity.py` asserts it; read `contracts/README.md`
for what it does and does not compare.

## Documentation

Every block's specification lives in this repository, beside the port it
describes, at `<block>/docs/<block>.md`. Start with `docs/overview.md`.

Each doc carries YAML front-matter (`id`, `name`, `group`, `status`) and then
the block's responsibility, contract, implementations, mermaid diagrams, test
coverage, development status, dependencies, and next work.

These were converted from the 14 spec modules previously kept at
`letta-code/dashboard/js/plans/voice-communication/specs/`. **This repository is
now the source of truth for them.** The documentation SPA under
`agent_blocks/spa_documentation/` stays a *generated view* — it has its own git
history and build pipeline, and vendoring it here would create two copies that
drift. Its generator should read these Markdown files rather than the old spec
modules.

The behavioral reference for the migration remains the live implementation at
`/home/adamsl/letta-code/dashboard`, and the durable working map is
`/home/adamsl/letta-code/notes_plans_handoffs/toyota_input_options_agent_blocks_map.md`.
Use that map to locate the current dashboard implementation, its tests and
backend routes, and the exact seams planned for Agent Block adoption.

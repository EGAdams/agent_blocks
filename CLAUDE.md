# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A scratch/experiment workspace of independent "agent blocks" — small Python
projects plus a set of agent system prompts. The workspace root is a Git
repository on `master`. `catherine_agent_sdk/` and `spa_documentation/` are
ignored nested Git repositories with their own histories; `claude_agent/` and
`prompt_templates/` are also standalone nested Git repositories but are
currently untracked (not yet added to `.gitignore`) rather than ignored —
don't assume either is part of the root history. There is no root package,
build system, or dependency manifest, so commands run from each project's
directory. `AGENTS.md` sets response-style rules (short, direct answers) that
apply alongside this file.

The top-level areas are:

- `claude_agent_adapter/` — `ClaudeAgentAdapter`, a `ConversationAgent` port
  implementation over `claude-agent-sdk`; the sibling adapter role to
  `voice_communication`'s (still-unimplemented) `LettaAgentAdapter`
- `catherine_agent_sdk/` — Claude Agent SDK probe script
- `claude_agent/` — an untracked nested repo, byte-identical to
  `catherine_agent_sdk/` (same `basic_agent.py` and `CLAUDE.md`); treat it as
  a duplicate checkout, not a second implementation
- `lancedb_memory/` — two generations of the same idea: the original RAVEN-style
  script (`3o_mini_chat_w_lance.py`, OpenAI), and `agent_memory/`, its ported
  successor on the Claude Agent SDK with Protocol-based ports. Also home to
  two further composition roots built on the same conventions: `finance_team/`
  (a 6-agent vendor-pattern/receipt-audit team) and `receptionist_team/`
  (Toyota, a conversational front end that delegates to `finance_team`)
- `mazda/` — planning-stage package for the Mazda document-verification agent;
  every subpackage is currently a stub naming the `typing.Protocol` it will
  eventually satisfy, mirroring `spa_documentation/mazda/`'s skeleton. See
  `mazda/MIGRATION_PLAN.md` and `mazda/README.md` before adding real code.
- `voice_communication/` — a **contracts-only skeleton** (Toyota Input Options)
  spanning two package roots, `ts/` (dashboard/browser) and `py/` (Pipecat
  service), linked only by the JSON wire format in `contracts/`. Read
  `voice_communication/CLAUDE.md` before working there — it explains why
  "activating a block" means adding an adapter beside its interface, never
  editing the interface catalogs.
- `solid_agent_systems/` — one directory per SOLID-principle review agent
- `spa_documentation/` — a generated plain-HTML documentation SPA in its own
  nested repository; read its `CLAUDE.md` before working there
- `prompt_templates/` — an untracked nested repo of ad hoc prompt scratch
  files used to drive Mazda/rol_finances report-generation work; not part of
  this workspace's architecture
- `mailbox/` — ignored local coordination material

## Running

```bash
cd claude_agent_adapter && ../lancedb_memory/.venv/bin/python tests/test_adapter.py
cd catherine_agent_sdk  && python3 basic_agent.py
cd lancedb_memory       && python3 3o_mini_chat_w_lance.py   # legacy, OpenAI
cd lancedb_memory       && .venv/bin/python main.py          # current, Claude SDK
```

The current agent has an offline test suite:

```bash
cd lancedb_memory
.venv/bin/python tests/test_agent.py
.venv/bin/python tests/test_lancedb_store.py
.venv/bin/python tests/test_embedding_profiles.py
```

Dependencies are not declared anywhere; install ad hoc:

- `basic_agent.py` → `claude-agent-sdk` (**not** installed in the default
  interpreter — importing it currently raises `ModuleNotFoundError`)
- `3o_mini_chat_w_lance.py` → `openai` (legacy pre-1.0 API surface:
  `openai.Embedding.create`, `openai.ChatCompletion.create`), `lancedb`,
  `pyarrow`, `pandas`

## `claude_agent_adapter/` — ClaudeAgentAdapter

Implements `ConversationAgent.submit(text, generation) -> AsyncIterator[AgentEvent]`
(`ports.py`) by wrapping `claude-agent-sdk`'s `query()`, the same pattern
`lancedb_memory/agent_memory/adapters/claude_model.py` already uses for its
`LanguageModel` port. `ConversationAgent` mirrors `IConversationAgent`
(`voice_communication/ts/src/conversation_agent/conversation_agent.ts`) on the
Python side; `models.py`/`ports.py` are local copies of that shape rather
than a cross-package import, since this workspace has no root package (see
above). `adapter.py` is the only file here allowed to import
`claude_agent_sdk` — that's the DIP boundary.

`ResultMessage.is_error` is checked before yielding a terminal event, same
as `basic_agent.py` below; a failed call raises `AgentCallFailed` instead of
being forwarded as if it succeeded. Read `claude_agent_adapter/CLAUDE.md`
before touching it, especially the note that this adapter is *reproducible*
(any instance can construct one from `adapter.py`'s recipe) but not
*addressable* — it never provides a channel back into whatever Claude Code
conversation happened to write or run it.

Offline tests (`tests/test_adapter.py`) fake `claude_agent_sdk.query()` with
real `AssistantMessage`/`ResultMessage` objects, so they need an interpreter
with `claude-agent-sdk` installed but make no network calls and need no API
key — see **Running** above.

## `catherine_agent_sdk/basic_agent.py` — Agent SDK probe

Single-turn `query()` against the Claude Agent SDK, then dumps the
`ResultMessage`: status, error flag, session id, per-model usage/context-window
stats, and cost. The point of the script is the result-inspection block —
`ResultMessage.is_error` is where auth/model/execution failures surface, since
they do *not* raise.

The comments at the top of the file matter: `ANTHROPIC_API_KEY` and
`ANTHROPIC_BASE_URL` must be **unset** for the SDK to use the local Claude Code
subscription auth rather than direct-API billing.

## `lancedb_memory/3o_mini_chat_w_lance.py` — vector-recall chatbot

An infinite REPL loop:

1. Embed user input (`text-embedding-ada-002`, 1536 dims).
2. Persist the turn as a uuid-named JSON file in the *nexus* directory.
3. Vector-search LanceDB for the `convo_length` (30) most similar past turns.
4. `load_conversation()` re-reads those turns' JSON files from disk, sorts
   chronologically, and joins them into the recalled transcript.
5. Fill the `prompt_response.txt` template (`<<CONVERSATION>>`, `<<MESSAGE>>`),
   call chat completion, log prompt+response to `gpt3_logs/`.
6. Persist and index the assistant turn the same way, then `tbl.add()` both turns.

Key design point: **LanceDB stores the vectors, but the JSON files on disk are
the source of truth for message text.** Both must be written for recall to work;
an emptied or missing nexus directory silently degrades recall to an empty
conversation (`load_conversation` prints `file not found` and skips).

External paths/files the script requires but which are **not in this repo**:

- `key_openai.txt` (cwd) — OpenAI API key
- `prompt_response.txt` (cwd) — prompt template
- `/home/adamsl/linuxBash/agents/nexus/` — per-turn JSON store
- `/home/adamsl/linuxBash/agents/run.txt` — alternate input when `USE_RUN_TEXT = True`
- `/tmp/lancedb` — LanceDB dir, table `youtube-chatbot`

These hardcoded absolute paths are load-bearing; changing one means changing both
the write site (`save_json` calls) and the read site (`load_conversation`).

## `lancedb_memory/agent_memory/` — the current agent

The successor to `3o_mini_chat_w_lance.py`. Read `lancedb_memory/README.md`
first; it documents the layout, the patterns, and the design decisions. Has its
own venv at `lancedb_memory/.venv` (pydantic, lancedb, pyarrow, claude-agent-sdk)
because the system interpreter is PEP 668 externally-managed.

The rules that matter when touching it:

- **`agent_memory/agent.py` may import `.models` and `.ports` and nothing else.**
  That single greppable rule is the DIP boundary. `lancedb`, `pyarrow`, and
  `claude_agent_sdk` appear only under `agent_memory/adapters/`, and `main.py`
  is the only module that names a concrete implementation.
- **LanceDB is the sole read path.** The table stores the message body; recall
  never touches the filesystem. `nexus/` is a write-only archive behind
  `NexusArchiveObserver`, kept so raw text outlives any particular vector store.
  This is the fix for the legacy script's dual-source-of-truth bug — deleting
  the nexus must not affect recall, and a test asserts it.
- **Embedding profiles must stay paired with their tables.** The default
  `hash` profile is lexical (`HashingEmbedder`, 256 dims); `sentence` uses a
  local semantic model (384 dims), and `openai` uses OpenAI embeddings (1536
  dims). `LanceDBMemoryStore` raises on a dimension mismatch rather than writing
  unsearchable rows, so each vector width requires its configured table.
- Tests run with no network and no API key. Run all three scripts listed in the
  root **Running** section, including `tests/test_embedding_profiles.py`.
- Paths all default inside the repo via `config.py` — do not reintroduce the
  legacy script's hardcoded `/home/adamsl/linuxBash/...` absolutes.

## `lancedb_memory/finance_team/` and `lancedb_memory/receptionist_team/`

Two more composition roots in `lancedb_memory/`, reusing its adapter
convention but not its `ConversationAgent` tool-free posture — see
`lancedb_memory/README.md` for full detail before touching either.

`finance_team/` does map-reduce vendor-payment-pattern analysis over the
`rol_finances` MySQL DB, partitioned by `expenses.method` (`CASH`/`CARD`/
`BANK`/`OTHER`), plus an independent receipt-image auditor. Its agents enable
tools (`Bash`/`Read`/`Write`/`Glob`/`Grep`, `permission_mode="bypassPermissions"`)
because the job requires querying a real DB and walking real receipt
directories — see `finance_team/adapters/tool_agent.py` for why that adapter
diverges from `agent_memory/adapters/claude_model.py`. Role briefs (system
prompt, task prompt, DB credentials) live in `finance_team/roles.py` as pure
data, no SDK import, matching `agent_memory/ports.py` staying Protocol-only.
Run with `.venv/bin/python -m finance_team.orchestrate` from `lancedb_memory/`
(`--skip-receipts`, `--only <agent>` also supported).

`receptionist_team/` is Toyota, a persistent conversational agent backed by
the unmodified `agent_memory.agent.ConversationAgent`, that delegates to
`finance_team` roles when a request calls for one via a `LanguageModel`
Decorator (`RoutingModel`, same shape as `decorators.py`'s `RetryingModel`) —
the underlying `ConversationAgent` never knows a delegation happened. Voice
mode reuses the dashboard's existing HTTP speech pipeline
(`POST /api/voice` / `POST /api/tts` on `localhost:8765`) rather than
reimplementing STT/TTS, and fails closed to typed I/O if the dashboard or mic
is unavailable. Run with `.venv/bin/python -m receptionist_team.main` from
`lancedb_memory/` (`--voice`, `--role-model <model>` also supported).

Both keep the same auth convention as the rest of the repo:
`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` stay unset so everything runs on
local Claude Code subscription (OAuth) auth.

## `solid_agent_systems/` — SOLID review agents

Intended layout, one directory per principle:

```
<principle_agent>/
  <principle_agent>.md   # the agent's system prompt
  src/<principle_agent>.py  # the runner
```

Only `dependency_inversion_master/` is filled in. Its `.md` is a complete,
carefully-specified system prompt worth reading before touching anything here —
it defines the house style these agents enforce:

- `typing.Protocol` for dependency abstractions (not ABCs), concrete types
  injected from a composition root/factory
- **Pydantic `BaseModel` is mandatory** for structured data crossing boundaries;
  no new dataclasses, `NamedTuple`s, or ad-hoc dicts as substitutes
- explicit type annotations on params, returns, and Protocol methods
- smallest reasonable repair only, and a fixed output format
  (`## DIP Result` → `VIOLATION` / `POSSIBLE VIOLATION` / `NO SIGNIFICANT VIOLATION`,
  then `## Reason`, `## Dependency`, `## Corrected Code`)
- an explicit anti-over-engineering rule: do not abstract `str`, `int`, `Path`,
  `datetime`, simple value objects, etc.

Known state, so you don't mistake it for a design: the other four
(`single_responsibility_guru`, `open_closed_sme`, `queen_liskov`,
`interface_segregation_king`) are **empty 0-byte `.md` stubs with no `src/`**,
and `dependency_inversion_master/src/dependency_inversion_master.py` is currently
a byte-for-byte (modulo spacing) copy of `catherine_agent_sdk/basic_agent.py` —
the prompt is not yet wired into it, and the copied script still asks
"Are you able to access the web?".

## Style

`basic_agent.py` and its copy use an unusual spacing convention inside call
parens and f-string braces (`print( f"Status: { final_result.subtype }" )`).
`3o_mini_chat_w_lance.py` uses a lighter, inconsistent variant of the same habit
(`print( 'file not found:', filename)`). Match the surrounding file rather than
normalizing it.

`agent_memory/` and everything under it (plus `basic_agent.py` and the
`dependency_inversion_master.py` copy) additionally enforce the closing-bracket
half of this rule strictly: every non-empty `)`, `]`, or `}` gets a space
before it — `str )`, `[ 0.0 ]`, `{ "a": 1 }` — while an empty pair stays
tight (`()`, `[]`, `{}`). This applies to every bracket type, not just call
parens. The space belongs only next to the innermost non-bracket content:
when closing brackets are adjacent, they stack with no space between them —
`] )` becomes `])`, and `] ) )` becomes `]))`, however deep the nesting.
When adding code to these files, write closing brackets this way from
the start rather than relying on a cleanup pass afterward.

The same rule mirrors on the opening side: every non-empty `(`, `[`, or `{`
gets a space after it, next to the innermost non-bracket content, and
adjacent opening brackets stack tight with no space between them —
`self._store.add([ user_turn, agent_turn ])`, not
`self._store.add( [user_turn, agent_turn ])`.

## Note

Nested repositories have their own `CLAUDE.md` files for local instructions.
Keep this root file authoritative for the root repository and shared project
architecture.

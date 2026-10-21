# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A scratch/experiment workspace for building agent "blocks" — small, standalone Python scripts and agent prompt definitions. There is no package, build system, dependency manifest, linter config, or test suite. Every script is run directly with `python3` from its own directory (several rely on relative paths in the cwd).

The root directory is **not** a git repo; `catherine_agent_sdk/` contains its own nested repo (`master`, no remote).

Three independent areas:

| Directory | What it is |
|---|---|
| `catherine_agent_sdk/` | Claude Agent SDK probe script |
| `lancedb_memory/` | RAVEN-style long-term-memory chatbot over LanceDB + OpenAI |
| `solid_agent_systems/` | One agent per SOLID principle: a prompt `.md` plus an optional `src/` runner |

## Running

```bash
cd catherine_agent_sdk && python3 basic_agent.py
cd lancedb_memory   && python3 3o_mini_chat_w_lance.py
cd solid_agent_systems/dependency_inversion_master/src && python3 dependency_inversion_master.py
```

Dependencies are not declared anywhere and **none are currently installed** in the default interpreter; install ad hoc:

- Claude Agent SDK scripts → `claude-agent-sdk`
- `3o_mini_chat_w_lance.py` → `openai` (legacy pre-1.0 API surface: `openai.Embedding.create`, `openai.ChatCompletion.create`), `lancedb`, `pyarrow`, `pandas`

For the SDK scripts, `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` must be **unset** (see the comments at the top of each file) so the SDK uses local Claude Code subscription auth rather than direct-API billing.

## `solid_agent_systems/` — agent layout

Each principle gets its own directory named after a persona:

```
<agent_name>/
  <agent_name>.md      # the agent's system prompt / role definition
  src/<agent_name>.py  # optional Claude Agent SDK runner
```

Only `dependency_inversion_master` is filled in. `queen_liskov`, `single_responsibility_guru`, `open_closed_sme`, and `interface_segregation_king` are **empty placeholder `.md` files** — writing one means following the structure of `dependency_inversion_master.md`, which is the reference template:

Role (one job, no unrelated refactoring) → the principle with before/after code → an ordered decision procedure → an explicit "do not over-engineer" section → the preferred Python mechanism → a fixed output contract (`## <X> Result` with exactly one of `VIOLATION` / `POSSIBLE VIOLATION` / `NO SIGNIFICANT VIOLATION`, then `## Reason`, `## Dependency`, `## Corrected Code`).

House rules those prompts impose on generated Python, which apply to code written in this repo too:

- `typing.Protocol` for dependency abstractions.
- Pydantic `BaseModel` for structured data crossing boundaries — **not** dataclasses, `NamedTuple`s, or ad-hoc dicts.
- Explicit type annotations on parameters, returns, Protocol methods, and new attributes.
- Smallest reasonable change; preserve behavior.

`src/dependency_inversion_master.py` is a **deliberate** copy of `catherine_agent_sdk/basic_agent.py`, kept as the starting point to build the real runner from — it is not yet wired to the sibling `.md` prompt (it still runs the hardcoded one-shot `query()`), and that is expected, not a bug to fix on sight. The other agents' runners will be forked from the same file; when changing the shared shape, check every copy.

## `lancedb_memory/3o_mini_chat_w_lance.py`

An infinite REPL implementing vector-recall chat:

1. Embed user input (`text-embedding-ada-002`, 1536 dims).
2. Persist the turn as a uuid-named JSON file in the *nexus* directory.
3. Vector-search LanceDB for the `convo_length` (30) most similar past turns.
4. `load_conversation()` re-reads those turns' JSON files from disk, sorts chronologically, joins them into the recalled transcript.
5. Fill the `prompt_response.txt` template (`<<CONVERSATION>>`, `<<MESSAGE>>`), call chat completion, log prompt+response to `gpt3_logs/`.
6. Persist and index the assistant turn the same way.

Key design point: **LanceDB stores the vectors, but the JSON files on disk are the source of truth for message text.** Both must be written for recall to work; a missing nexus directory silently degrades recall to empty strings (the script prints `file not found:` and continues).

External paths and files the script requires but which are **not in this repo** (and do not currently exist on this machine):

- `key_openai.txt` (cwd) — OpenAI API key
- `prompt_response.txt` (cwd) — prompt template
- `/home/adamsl/linuxBash/agents/nexus/` — per-turn JSON store
- `/home/adamsl/linuxBash/agents/run.txt` — alternate input when `USE_RUN_TEXT = True`
- `/tmp/lancedb` — LanceDB dir, table `youtube-chatbot`

These hardcoded absolute paths are load-bearing; changing one means changing both the write site (`save_json`) and the read site (`load_conversation`).

## `catherine_agent_sdk/basic_agent.py`

Single-turn `query()` against the Claude Agent SDK, then dumps the `ResultMessage`: status, error flag, session id, per-model usage/context-window stats, and cost. The point of the script is the result-inspection block — `ResultMessage.is_error` is where auth/model/execution failures surface, since they do *not* raise.

## Style

The Agent SDK scripts use an unusual spacing convention inside call parens and f-string braces (`print( f"Status: { final_result.subtype }" )`), and it is applied inconsistently between the two copies. Match the surrounding file rather than normalizing it.

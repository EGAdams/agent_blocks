# AGENTS.md

## Response style

- Give the user short, simple, direct answers.
- Lead with the answer and include only the details needed to understand or
  verify it.
- Avoid long explanations, repeated conclusions, and unnecessary formatting.
- Use plain language. Explain technical terms only when they are necessary.

## Repository overview

This workspace collects small, mostly independent experiments around agent
systems. The root directory is a Git repository on `master`, and it also
contains ignored nested Git repositories under `catherine_agent_sdk/` and
`spa_documentation/`.

The main areas are:

- `lancedb_memory/`: a tested conversational-memory agent, plus the legacy
  `3o_mini_chat_w_lance.py` prototype.
- `solid_agent_systems/`: prompts and runners for SOLID-principle review agents.
- `catherine_agent_sdk/`: a nested repository containing a Claude Agent SDK
  probe. It has its own `CLAUDE.md`.
- `spa_documentation/`: an ignored nested repository containing the generated
  documentation SPA. Read its `CLAUDE.md` before working there.
- `mailbox/`: ignored local coordination material.

## Toyota Input Options and Voice Communication

New Toyota Input Options development belongs in `voice_communication/`, with
its visual documentation mirrored in `spa_documentation/voice_communication/`.
Before working there, read the durable implementation and migration map at:

`/home/adamsl/letta-code/notes_plans_handoffs/toyota_input_options_agent_blocks_map.md`

The existing `/home/adamsl/letta-code/dashboard` implementation is the
behavioral reference and migration source, not the primary location for new
Agent Block work.

There is no root package or dependency manifest. Run Python entry points and
tests from their owning directories.

## `lancedb_memory`

Read `lancedb_memory/README.md` before changing this project. Its current agent
uses Protocol-based ports, Pydantic boundary models, concrete adapters, and a
composition root in `main.py`.

Important boundaries:

- `agent_memory/agent.py` may import only `.models` and `.ports` from the
  application package.
- Concrete integrations belong in `agent_memory/adapters/`; `main.py` selects
  and wires them.
- LanceDB is the read path. The nexus JSON archive is write-only and must not be
  required for recall.
- Keep paths configurable and repository-local; do not restore the legacy
  prototype's absolute `/home/adamsl/linuxBash/...` paths.
- Changing embedding dimensions requires a matching table/profile.

Run the full offline test set from `lancedb_memory/`:

```bash
.venv/bin/python tests/test_agent.py
.venv/bin/python tests/test_lancedb_store.py
.venv/bin/python tests/test_embedding_profiles.py
```

Run the current application with:

```bash
.venv/bin/python main.py --session default
```

## `solid_agent_systems`

Each review agent is intended to have a prompt at
`<agent>/<agent>.md` and a runner at `<agent>/src/<agent>.py`.
`dependency_inversion_master/` is currently the only populated implementation;
the other four prompt files are empty stubs.

Follow the established prompt rules when extending these agents: use
`typing.Protocol` for dependency abstractions, Pydantic models for structured
boundary data, explicit annotations, and the smallest reasonable repair.

## Style and change hygiene

- Preserve the unusual bracket-spacing style in existing Python files instead
  of normalizing unrelated code.
- Keep edits scoped. The worktree may already contain user changes; do not
  overwrite or revert them.
- Use `rg`/`rg --files` for searches.
- Do not assume ignored nested repositories are part of the root Git history.

## Codex CLI status line

For Codex CLI 0.150.1, do not use the unsupported
`context-window-size` status-line item. Use:

```toml
[tui]
status_line = ["model-with-reasoning", "context-used", "context-remaining", "task-progress"]
status_line_use_colors = true
```

The built-in context fields are textual; this version cannot configure a
custom segmented bar or fixed 0–120k scale. Re-check supported identifiers
after upgrading Codex.

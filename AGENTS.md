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

## Handoff from Claude (2026-10-28) — browser recorder, unfinished

Nothing below is committed. Start with `git status` and `git diff` in this
repository, plus `git -C /home/adamsl/rol_finances diff menu_code/` and
`git -C /home/adamsl/letta-code diff dashboard/`. Read every untracked file
under `adws/` changed in the last few hours: `find adws -newermt '-4 hours' -type f`.
Then read `adws/browser_workflows/DESIGN.md`.

Done: the Chrome click recorder and playback engine (`adws/browser_workflows/`,
`adws/adw_browser_*.py`, `adws/browser_workspace_menu.py`, mounted in
`rol_finances` menu_code). Its 69 tests pass. A post-office card
(`browser-recorder` in `lancedb_memory/agent_comms/agents.json`, `adw` runtime
in `factory.py`) lets agents run a saved playback.

Finish it:
1. **Fix the SyntaxError in `lancedb_memory/agent_comms/tests/test_post_office.py`**
   (around line 400). My last edit put the new
   `test_a_timed_out_adw_is_ended_with_its_whole_process_group` *inside* the
   `try:` of `test_browser_playback_reaches_the_recorder_adw_and_reports_its_verdict`.
   Move the `finally: h.close()` back so it closes the first test before
   the new one starts.
2. Run the suites from `lancedb_memory/`:
   `uv run --quiet --with pydantic python -m agent_comms.tests.test_post_office`
   (and `test_operations`, `test_alerts`). Also run
   `cd adws && uv run --quiet --with pydantic --with pytest --with aiohttp pytest -q browser_workflows/tests`.
   I rewrote `AdwSubprocessExecutor` (`agent_comms/executors/mechanic.py`) with
   one `command` param, process-group kill on timeout, `diagnose_flag`, and
   `sender_flag`. I also changed the list-question regex in
   `adws/browser_workflows/operations/library_ops.py`. None of this is tested
   yet. If a test name still says `"browser"`, change it to `"browser-recorder"`.
3. **Recreate `lancedb_memory/.venv`.** It is missing, so detached post-office
   workers crash (`No module named 'dotenv'`). That breaks every agent on this box.
   The user wants one or two "close" venvs that can be topped up with what is
   missing. Packages are listed in `lancedb_memory/CLAUDE.md` → Environment:
   pydantic, lancedb, pyarrow, claude-agent-sdk, openai, sentence-transformers,
   pymysql, python-dotenv, pyyaml, rich, selenium, plus aiohttp for the recorder
   service. Python 3.14. e.g. `uv venv --python 3.14 lancedb_memory/.venv && uv pip install --python lancedb_memory/.venv/bin/python ...`.
   Ask the user before installing anything large (torch, via
   sentence-transformers).
4. Update the root `CLAUDE.md` adws paragraph. It names the card `browser`;
   the card is now `browser-recorder`. Also check `agent_comms/README.md` and
   the final section of `DESIGN.md` for the same rename.
5. Ask the user before committing. The commit should cover this work, the Fifth
   Third "Log In" click ADWs (`adw_interface_browser_click.py`,
   `adw_interface_browser_debug_open.py`, `adws/browser_session/`), and the
   WSLInterop dashboard tab in `letta-code/dashboard`. They also haven't said
   whether to deploy that tab to DESKTOP-2OBSQMC.

## Codex follow-up (2026-10-28) — browser recorder verified

- Fixed the misplaced `finally` in `test_post_office.py` and updated the
  remaining `browser` card references to `browser-recorder`.
- Hardened ADW timeout cleanup: always SIGKILL the remaining process group
  after TERM, even when the parent has exited and a child redirects its
  output and ignores TERM. The regression test covers that case.
- Added four routed list-playback question cases; none attaches to Chrome.
- Restored `lancedb_memory/.venv` with Python 3.14.7 and all listed runtime
  dependencies, including `aiohttp`, `pytest`, and (with user approval)
  `sentence-transformers` plus CPU-only `torch`. Rebuild commands are now
  in `lancedb_memory/CLAUDE.md` under Environment.
- Verified under that venv: post office 20, operations 6 (no dependency
  skips), alerts 8, memory suites 9 + 5 + 5, browser-session 25, and recorder
  74 with `BW_E2E=1` (including the real Chrome record/save/replay test).
  A detached hello-world worker also completed using the restored venv;
  its mailbox and logs were isolated in a temporary directory.
  The semantic embedder also produced a 384-dimensional vector from the
  cached MiniLM model with Hub access disabled; no model download was needed.
- User approved committing and pushing the browser work, finance menu, and
  WSLInterop dashboard changes on 2026-10-28. Deployment to DESKTOP-2OBSQMC
  remains a separate decision. Preserve unrelated `catherine_agent_sdk` and
  `voice_metrics.log` changes. User instructions are in
  `adws/browser_workflows/README.md`.

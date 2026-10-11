# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Mazda: a second, independent receptionist agent living under
`lancedb_memory/`, built on the same `agent_memory.agent.ConversationAgent`
engine as `receptionist_team`'s Toyota but otherwise unrelated to
`finance_team`/`receptionist_team` — see the root `CLAUDE.md` and
`lancedb_memory/README.md` for the shared conventions this package reuses
(Protocol ports, DIP import boundaries, the `agent_memory` engine). Mazda
routes user requests to her own specialist roles: `sheeta` (spreadsheet
creation), `tarita` (tax-form extraction into IRS Form 1040 fields), and
`michi` (deterministic finance-report progress tracking). All commands below
run from `lancedb_memory` (one level up), not from this directory, since
that's the package's working root and where the shared `.venv` and `tests/`
harness live.

## Running

```bash
cd lancedb_memory
.venv/bin/python -m mazda_team.main                    # Mazda's REPL (typed I/O)
.venv/bin/python -m mazda_team.main --voice             # mic in / spoken out
.venv/bin/python -m mazda_team.orchestrate               # run sheeta + tarita directly, no receptionist
.venv/bin/python -m mazda_team.orchestrate --only sheeta
.venv/bin/python -m mazda_team.progress.cli               # print Michi's snapshot, no LLM call
```

`main.py` loads `mazda_team/.env` itself (no `python-dotenv` in the venv) —
copy `.env.example` to `.env` and set `GROQ_API_KEY` (required) and
`PIPECAT_PILOT_AGENT_ID` (optional, faster Groq-backed STT).

### Tests (offline, no network/API key)

```bash
cd lancedb_memory
.venv/bin/python -m mazda_team.tests.test_michi_routing
.venv/bin/python -m mazda_team.tests.test_progress_tracker
.venv/bin/python -m mazda_team.tests.test_roles_registration
```

Each file is runnable directly too (`.venv/bin/python mazda_team/tests/test_progress_tracker.py`)
via the `if __name__ == "__main__"` block that calls `mazda_team.tests.runner.main`.
`tests/runner.py` is a tiny async harness: any module-level `test_*` coroutine
is discovered and run, no pytest involved. `ScriptedModel` (a queued-reply
fake `LanguageModel`) lives in the shared `lancedb_memory/tests/fakes.py`,
not duplicated here.

## Architecture

### Two-model split (Mazda's main divergence from Toyota)

- **Conversation/routing** — served by **Groq** (`agent_memory/adapters/groq_model.py`,
  default `openai/gpt-oss-120b`), not Claude. This is what talks to the user
  and decides whether to route.
- **Delegated specialist work** — goes through the Claude Agent SDK
  (`adapters/tool_agent.py`, `ClaudeAgentOptions(permission_mode="bypassPermissions")`,
  same shape as `finance_team/adapters/tool_agent.py`). Only sheeta/tarita
  use this path; Michi never does (she's deterministic + a narration LLM
  call, see below).

Both keep the same repo-wide auth convention: `ANTHROPIC_API_KEY` /
`ANTHROPIC_BASE_URL` stay **unset** so everything runs on local Claude Code
subscription (OAuth) auth, not metered API billing. `tool_agent.py` also
explicitly zeroes both env vars per-subprocess (`env={"ANTHROPIC_API_KEY": "",
"ANTHROPIC_BASE_URL": ""}`) as a backstop against an ambient key leaking in
from the caller's shell.

### Routing: `RoutingModel` (`adapters/routing_model.py`)

A `LanguageModel` Decorator (same shape as `agent_memory/adapters/decorators.py`'s
`RetryingModel`) wrapping the Groq conversational model. `ConversationAgent`
only ever sees one `complete()` call and one `AgentReply` — it never knows a
delegation happened. Mazda's system prompt (`roles.py`'s `MAZDA_SYSTEM_PROMPT`)
tells the LLM to reply with a bare control line when routing:

```
ROUTE: <agent_name>
ROUTE: <agent_name> | <extra instructions>
```

`RoutingModel.complete()` matches this with `re.fullmatch` (not `search`) so
ordinary conversation that merely *mentions* routing never triggers it. On a
match:
- `michi` → calls `progress.michi.answer()` directly (deterministic tracker +
  narration LLM), never touches `tool_agent.py`.
- `sheeta`/`tarita` → looked up in `specialist_roles.ROLES`, optionally has
  `extra` instructions appended to its task prompt, then runs via
  `adapters/tool_agent.run_role()`.
- unknown name → a friendly error listing all known agents, not a crash.

### Michi: deterministic tracker + constrained narrator (`progress/`)

Split by design so status claims are never hallucinated:

- `schema.py` — Pydantic boundary models only (`FinanceReport` on-disk
  contract, `AccountPeriodStatus`, `NextStepCandidate`, `ProgressSnapshot`).
- `tracker.py` — **no LLM calls.** Walks `<repo_root>/finance_reports/<year>/<month>/<account>.json`,
  classifies each account/period as `not_started` / `in_progress` / `blocked`
  / `complete`, flags stale unresolved reports, unexpected accounts, and path/
  content mismatches, and derives ranked `next_steps` (severity order:
  `blocker` > `missing` > `human_judgment` > `ready`). Persists the snapshot
  to `progress/michi_state.json` under the repo root.
- `narrate.py` — `MICHI_SYSTEM_PROMPT` instructs the explainer LLM to use
  *only* the snapshot's facts, and never claim something is complete unless
  `status="complete"` **and** `verification="verified"`. `build_explainer_prompt()`
  serializes the whole snapshot (entries, missing/outdated, human-judgment
  items, next steps) into the prompt.
- `michi.py` — the composition point: `answer()` calls `tracker.collect_and_persist()`
  then hands the snapshot to the supplied explainer `LanguageModel`.

A progress question routes to Michi even if the user names sheeta or tarita
by name — she only *reads* their output, never writes a report or form
herself (enforced in `roles.py`'s system prompt text, not code).

### Specialist roles (`specialist_roles.py`)

Pure data, no SDK import — `AgentRole` dataclasses (`system_prompt`,
`task_prompt`, `allowed_tools`, `max_turns`) matching `agent_memory/ports.py`'s
convention of staying Protocol/data-only. `REPO_ROOT` (env override
`MAZDA_TEAM_REPO_ROOT`) defaults to `mazda_team/workspace/` as a
self-contained placeholder — no live database or tax-intake repository is
wired up yet; both roles' prompts explicitly forbid inventing figures or
touching `rol_finances`.

### Registration invariant (`roles.py`)

`ALL_AGENT_NAMES = sorted(set(ROLES) | {MICHI_AGENT_NAME})`, and a startup
loop raises `RuntimeError` if `ROLE_BLURBS` / `ROLE_SPOKEN_NAMES` /
`ROLE_START_TASKS` is missing an entry for any registered agent — adding a
new specialist without populating all three dicts fails fast at import time
rather than producing a `KeyError` mid-conversation.

### Voice I/O (`adapters/voice_io.py`, `adapters/metrics.py`)

Reuses the dashboard's existing HTTP speech pipeline (`POST /api/voice` /
`POST /api/tts` on `localhost:8765`) rather than reimplementing STT/TTS —
same pattern as `receptionist_team`. `MazdaVoiceIO` only differs from the
shared `DashboardVoiceIO` by its edge-tts voice (`MAZDA_EDGE_TTS_VOICE`,
default `en-US-AriaNeural`, vs. Toyota's `en-GB-SoniaNeural`). Fails closed
to typed I/O (`VoiceUnavailableError`) if the dashboard or mic is
unavailable — never silently substitutes a guessed transcript or skips a
reply. Recording is silence-terminated (via `parecord` reading raw PCM off
PulseAudio, since WSLg exposes the mic as `RDPSource`, not an ALSA card),
not fixed-duration; TTS is synthesized and played sentence-by-sentence with
one-sentence look-ahead prefetch to hide latency behind playback.
`adapters/metrics.py`'s `Stopwatch`/`log_stage` write Mazda's own
`voice_metrics.log` in this directory (distinct from the dashboard's own
stage logging).

## Style

Files carrying the `# unset ANTHROPIC_API_KEY` / `# unset ANTHROPIC_BASE_URL`
header comments (`main.py`, `orchestrate.py`, `adapters/tool_agent.py`) and
`orchestrate.py`/`adapters/tool_agent.py` additionally follow the repo-wide
closing/opening-bracket spacing convention documented in the root
`CLAUDE.md` (`str )`, `[ 0.0 ]`, tight stacked brackets `])`). Newer files in
this package (`roles.py`, `adapters/routing_model.py`, `adapters/voice_io.py`,
`progress/*`, `tests/*`) do **not** use that spacing — match whichever
convention the file you're editing already uses rather than normalizing it.

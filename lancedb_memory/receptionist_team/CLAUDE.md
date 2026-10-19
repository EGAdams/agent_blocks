# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

**Toyota** — the conversational front door to `finance_team`'s headless
roles. Seven source files (`main.py`, `roles.py`, `service.py`,
`service_models.py`, `adapters/{routing_model,voice_io,metrics}.py`);
everything else it needs comes from `agent_memory/` (the engine) and
`finance_team/` (the roles it delegates to).

She has **two front ends over one `build_toyota()`**: the terminal REPL
(`main.py`) and a loopback HTTP sidecar (`service.py`) that the letta-code
dashboard talks to. See *The sidecar* below before touching either.

Read in this order, preferring the more specific on conflict:
`../../CLAUDE.md` (workspace) → `../CLAUDE.md` (commands, invariants,
known doc drift) → `../README.md` (narrative) → this file. `../mazda_team/CLAUDE.md`
describes Mazda, Toyota's near-twin — the two packages are structurally
parallel and a change made here usually has a counterpart there, but they
share no code except `agent_memory`.

## Running

All commands run from `lancedb_memory/` (one directory up), never from here.
That directory holds both halves of what this package needs: the `.venv`
(Python 3.14) with every dependency, and the import root — `receptionist_team`,
`finance_team`, and `agent_memory` are sibling packages *inside* it, so it is
the only cwd where `-m receptionist_team.main` resolves. Running it from this
directory fails with `No module named 'receptionist_team'`, and invoking the
interpreter as `../.venv/bin/python` additionally trips `sys.prefix` warnings.
Bare `python3` has none of the dependencies (PEP 668 externally-managed), so
always use `.venv/bin/python`.

```bash
cd ..
.venv/bin/python -m receptionist_team.main                     # typed chat
.venv/bin/python -m receptionist_team.main --voice             # mic in / spoken out
.venv/bin/python -m receptionist_team.main --session toyota --embedding-profile sentence
.venv/bin/python -m receptionist_team.main --role-model claude-haiku-4-5-20251001
.venv/bin/python -m receptionist_team.service --port 8767   # the dashboard sidecar
```

Normally you don't run `service.py` by hand — it is the user unit
`toyota-receptionist.service` (`systemctl --user status toyota-receptionist`,
log `/tmp/toyota_receptionist.log`), restartable from the dashboard's Server
Management tab. A hand-started copy will collide on the port with the unit.

Tests cover the sidecar's turn loop only:

```bash
.venv/bin/python -m receptionist_team.tests.test_service_turns
```

They inject a fake agent, so no LanceDB, no Groq, no network. Everything
*below* the sidecar is still verified by running the REPL: `handle_turn` goes
to a real LanceDB table, and a delegation runs the Claude Agent SDK with tools
against the live MySQL DB. The async harness is `../tests/runner.py` (shared
with `mazda_team/tests/`, which is where the parallel pieces get covered).

`main.py` parses `receptionist_team/.env` itself via `os.environ.setdefault`
(no `python-dotenv` in the venv, and there's no `.env.example` here — copy
`../mazda_team/.env.example`). `GROQ_API_KEY` is required (Toyota's own replies
*and* STT); `PIPECAT_PILOT_AGENT_ID` is optional and only speeds up STT.
Don't add a `source .env` step to docs — `setdefault` already means an
exported value wins.

There is no `requirements.txt` or lockfile anywhere in the workspace;
`../.venv/bin/python -m pip list` is the only manifest. Worth knowing before
you go looking for a missing dependency: **there is no `groq` package, and
there never needs to be.** `agent_memory/adapters/groq_model.py` uses the
installed `openai` SDK with `base_url="https://api.groq.com/openai/v1"`,
since Groq's API is OpenAI-compatible. Likewise `adapters/voice_io.py` uses
only stdlib (`urllib.request`, `wave`, `array`, `subprocess`) — its real
dependencies are the external binaries `parecord` and one of
`ffplay`/`mpg123`/`paplay`, not Python packages.

## Architecture

### Toyota is Groq; only delegated roles are Claude

`main.py` builds `GroqLanguageModel` (`--model`, default `openai/gpt-oss-120b`)
for conversation and routing — one HTTP call per turn, no `claude_agent_sdk`
subprocess spawn. Claude is reserved for delegated finance work
(`--role-model`, default `claude-sonnet-5`), which needs real Bash/Read/Write
tool use. The root `CLAUDE.md` and `README.md` still describe this split as
*Mazda's* divergence; it applies to Toyota too.

`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` must stay unset (local Claude Code
OAuth, not metered API) — same repo-wide rule; `finance_team/adapters/tool_agent.py`
holds the per-subprocess `env=` backstop.

### The composition root and the one cross-team import

`main.py` is the only module here allowed to name a concrete implementation.
The deliberate cross-team edge is `adapters/routing_model.py` importing
`finance_team.adapters.tool_agent.run_role` and `finance_team.roles`
(`ROLES`, `REPO_ROOT`) — Toyota's whole purpose. `roles.py` imports
`finance_team.roles.ROLES` for names only, nothing SDK- or DB-shaped.

### Routing is a `LanguageModel` Decorator, and it is live

`RoutingModel` wraps the Groq model and satisfies the same `LanguageModel`
Protocol, so `ConversationAgent` sees one `complete()` and one `AgentReply`
and never learns a delegation happened (same shape as
`agent_memory/adapters/decorators.py`'s `RetryingModel`).

```
ROUTE: <agent_name>
ROUTE: <agent_name> | <extra instructions>
```

matched with `re.fullmatch`, **not** `search` — ordinary conversation that
merely mentions routing must not fire a delegation. Keep it that way: a
matched ROUTE line calls `run_role(..., cwd=REPO_ROOT)` with
`permission_mode="bypassPermissions"` against `/home/adamsl/rol_finances`
and the real `rol_finances` MySQL DB. Extra instructions are appended to a
`dataclasses.replace`'d copy of the role, never mutated in place. An unknown
agent name returns a friendly message listing known agents rather than
raising.

### `roles.py` fails loud at import on an unregistered role

`ROLE_BLURBS`, `ROLE_SPOKEN_NAMES`, and `ROLE_START_TASKS` are each checked
against `finance_team.roles.ROLES` at module import; a missing key raises
`RuntimeError` immediately. So **adding a role to `finance_team/roles.py`
breaks Toyota's import until all three dicts here gain an entry** — that's
the intended failure, since a role the receptionist can't name or describe
is a role it shouldn't be routing to. `TOYOTA_SYSTEM_PROMPT` builds its
roster and its allowed-agent-names list from `ROLES` too, so no third list
needs editing.

### The delegation acknowledgment, and what is still only half-wired

`TOYOTA_SYSTEM_PROMPT` tells the model to emit a bare ROUTE line because
"the application supplies the spoken acknowledgment separately so that it can
begin playback immediately while the delegated agent starts working." That is
now true on one path and still not on the other.

`RoutingModel.__init__` takes an optional `on_delegation: Callable[[str], None]`
and calls it with `roles.delegation_acknowledgment(role_name)` *before*
awaiting `run_role`. The sidecar passes one (see below), so a dashboard user
hears "let me check with accounting" within a second and the reply lands
minutes later on the same stream. **The CLI does not** — `main.py` leaves
`on_delegation=None`, so `--voice` still goes silent for the whole delegation.
Wiring `voice_io.speak()` into that hook is the remaining piece; the hook
itself is no longer the missing part.

The callback is synchronous and fire-and-forget on purpose: it runs inside
`complete()`, so anything slow or raising there would delay or break the very
turn it is announcing.

### The sidecar

`service.py` is a `ThreadingHTTPServer` holding **one warm
`ConversationAgent`**, because loading the sentence-transformers model and the
LanceDB table costs ~30 seconds — per process, not per turn. Two endpoints:
`GET /health` (the dashboard's tile probe) and `POST /turn`, which streams
NDJSON `service_models.TurnRecord`s: zero or more `acknowledgment`s, then a
`reply`, then a terminal `result` — or a single `error`.

Four things here are load-bearing:

- **It binds `127.0.0.1` and must keep doing so.** A delegated turn runs
  Claude with `bypassPermissions` and real Bash against the live
  `rol_finances` MySQL DB. The dashboard that calls it is `0.0.0.0`-bound and
  Tailscale-served to a phone; loopback-only binding *is* the authorization
  model. Do not make it configurable.
- **Turns are serialized** by an `asyncio.Lock`. `handle_turn` recalls before
  it persists, which only yields a sane transcript if turns don't interleave —
  and HTTP will happily deliver two at once.
- **The turn runs on its own loop thread; records cross back on a
  `queue.Queue`** drained by the HTTP thread. That is what lets the
  acknowledgment reach the browser while `handle_turn` is still blocked
  inside `run_role`.
- **A dropped client does not abort the turn.** `BrokenPipeError` closes the
  generator and lets the delegation finish — a half-run finance role that
  already wrote files is worse than a wasted reply.

`_TurnRunner` takes its agent in `__init__` and builds the real one in
`build()`, so `tests/test_service_turns.py` drives the loop with a fake and
never imports torch. Port 8767 is deliberate: 8765 is the dashboard, 8766 is
`planner/a2a_communicating_agents/thought_bridge.py`.

The consumer lives in the other repo —
`letta-code/dashboard/toyota_service.py` translates these records into the
same envelope `/api/letta-code-stream` emits and serves them at
`/api/receptionist-turn`. **The wire format is a contract across two
repositories**: changing `TurnRecord.kind` means changing `_translate` there
too, and the dashboard's `tests/test_toyota_service.py` is where the
translation is pinned.

### Voice is borrowed, not implemented

`adapters/voice_io.py` only speaks HTTP to the dashboard on `localhost:8765`
(`POST /api/voice` for STT, `POST /api/tts` for edge-tts) and imports nothing
from `dashboard/`, so a dashboard refactor can't break this package's imports
— but `dashboard-server.service` must be running or `--voice` falls back to
typed input for that turn. Specifics that cost real debugging time and are
documented in the file's own comments:

- Recording is **silence-terminated**, via `parecord --raw` to a temp file
  polled frame by frame (WSLg exposes the mic as a PulseAudio `RDPSource`,
  not an ALSA card; `parecord ... -` to a pipe buffers in bursts and is
  useless for level reading). The mic idles around 1700–2400 RMS here, hence
  the warm-up discard, the adaptive floor, and `SPEECH_MARGIN = 1200`.
- A dead `parecord` raises `VoiceUnavailableError` rather than looking like
  "nobody spoke" for 8 seconds every turn.
- TTS pipelines sentence N+1's synthesis behind sentence N's playback;
  `tts_wait_stall > 0` in the metrics names the sentence that fell behind.
- Everything in `speak()` fails closed — the reply is always printed first,
  so a missing player or a bad sentence skips audio, never the conversation.
- `ffplay` gets `stdin=DEVNULL` plus a timeout; inheriting the terminal's
  stdin can wedge it on SIGTTIN despite `-autoexit`.

### Memory

Standard `agent_memory` wiring, with two Toyota-specific choices in `main.py`:
`--embedding-profile` defaults to `sentence` (384-dim, local semantic model,
table `conversation_memory_sentence384`) because real human chat needs
meaning-based recall, and `Settings` is `model_copy`'d so the write-only
archive lands in `nexus/toyota/`. Recall still reads LanceDB only — don't
reintroduce a filesystem read into that path.

## Style and housekeeping

- **This package does not follow the workspace bracket-spacing convention**
  (`str )`, `[ 0.0 ]`). `agent_memory/`, `tests/`, and `finance_team`'s
  `orchestrate.py`/`tool_agent.py` do. Match the file you're editing; never
  normalize across the boundary.
- `voice_metrics.log` is **tracked in git** and appended to by every voice
  run, so `--voice` always dirties the worktree. Don't commit it as part of
  an unrelated change.
- `adapters/voice_io.py` imports `log_stage` but only uses `Stopwatch`
  (same in `mazda_team`) — harmless, but don't take the import as evidence
  of a second logging path.

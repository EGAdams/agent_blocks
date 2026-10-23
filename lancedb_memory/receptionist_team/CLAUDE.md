# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

**Two receptionists, one engine.** **Toyota** is the conversational front door
to `finance_team`'s headless roles; **Mazda** is a document-verification
receptionist whose specialists are Letta agents this package can't reach yet.
They are not two packages — they are two `ReceptionistProfile` values in
`profiles.py`, both assembled by `main.py`'s `build_receptionist()` and both
selected at the command line with `--agent {toyota,mazda}`. (`mazda_team/` used
to be a separate package; it was retired, and re-creating it would undo the
point.)

Source files, by job:

| file | job |
|---|---|
| `profiles.py` | `ReceptionistProfile` — everything that differs between the two |
| `rosters.py` | `Roster` — which agent names a receptionist may route to |
| `delegation.py` | `IDelegationRunner`, `UnwiredRunner`, `RunnerMap` — *how* a route runs |
| `roles.py` / `extra_roles.py` | Toyota's roster wording, blurbs, spoken names, extra `AgentRole`s |
| `mazda_roles.py` | Mazda's six specialists (five stubs + Michi) and her rendered prompt |
| `main.py` | the composition root: `build_receptionist()`, `build_toyota()`, `repl()` |
| `service.py` / `service_models.py` / `turn_runner.py` | the loopback HTTP sidecar the dashboard talks to |
| `adapters/` | `routing_model`, `handoff_model`, `tool_agent_runner`, `michi_runner`, `voice_io`, `metrics` |
| `progress/` | Michi: `tracker` reads report files, `narrate` prompts, `michi` composes the two |
| `operations/` | Toyota-only `WorkflowModel` stack (reports/tax intents, assignment policies) |
| `memory_desk.py` | the shared-memory recall wiring both receptionists use |

Everything else comes from `agent_memory/` (the engine) and `finance_team/`
(the roles Toyota delegates to).

Plus `agents/` — **one folder per agent, both receptionists included**, holding
the prompts, model config, tool declarations and documentation that used to be
Python literals. `agents/README.md` is authoritative for that subtree; it has
its own style rules and its own loader. Read it before editing anything under
`agents/`, and the `/new-receptionist-agent` skill before adding an agent.

There are **two front ends over one `build_receptionist()`**: the terminal REPL
(`main.py`) and a loopback HTTP sidecar (`service.py`) that the letta-code
dashboard talks to. The sidecar is Toyota-only today. See *The sidecar* below
before touching either.

Read in this order, preferring the more specific on conflict:
`../../CLAUDE.md` (workspace) → `../CLAUDE.md` (commands, invariants,
known doc drift) → `../README.md` (narrative) → this file.

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
.venv/bin/python -m receptionist_team.main                     # Toyota, typed chat
.venv/bin/python -m receptionist_team.main --voice             # mic in / spoken out
.venv/bin/python -m receptionist_team.main --agent mazda        # Mazda, same engine
.venv/bin/python -m receptionist_team.main --agent mazda --voice
.venv/bin/python -m receptionist_team.main --session toyota --embedding-profile sentence
.venv/bin/python -m receptionist_team.main --role-model claude-haiku-4-5-20251001
.venv/bin/python -m receptionist_team.progress.cli             # Michi's snapshot, no LLM
.venv/bin/python -m receptionist_team.service --port 8767   # the dashboard sidecar
```

Normally you don't run `service.py` by hand — it is the user unit
`toyota-receptionist.service` (`systemctl --user status toyota-receptionist`,
log `/tmp/toyota_receptionist.log`), restartable from the dashboard's Server
Management tab. A hand-started copy will collide on the port with the unit.

Tests cover the sidecar's turn loop, the agent folders, both profiles, the
delegation acknowledgment, the operations stack and Michi's tracker — sixteen
standalone scripts in `tests/`, each run as a module from `..`:

```bash
.venv/bin/python -m receptionist_team.tests.test_service_turns
.venv/bin/python -m receptionist_team.tests.test_mazda_profile
.venv/bin/python -m receptionist_team.tests.test_delegation_acknowledgment
.venv/bin/python -m receptionist_team.tests.test_michi_routing
.venv/bin/python -m receptionist_team.tests.test_progress_tracker
.venv/bin/python -m receptionist_team.agents.tests.test_agent_catalog
.venv/bin/python -m receptionist_team.agents.cli validate
```

They inject fakes, so no LanceDB, no Groq, no Claude SDK, no network —
`test_mazda_profile.py` deliberately builds Mazda's whole routing stack
*without* importing `claude_agent_sdk`, which only holds because
`profiles.py`'s runner factories import their adapters inside the function.
Everything *below* the sidecar is still verified by running the REPL:
`handle_turn` goes to a real LanceDB table, and a Toyota delegation runs the
Claude Agent SDK with tools against the live MySQL DB. The async harness is
`../tests/runner.py` and the shared fakes are `../tests/fakes.py`.

`main.py` parses `receptionist_team/.env` itself via `os.environ.setdefault`
(no `python-dotenv` in the venv); copy `.env.example` beside it. `GROQ_API_KEY`
is required (the receptionist's own replies *and* STT);
`PIPECAT_PILOT_AGENT_ID` is optional and only speeds up STT;
`<NAME>_EDGE_TTS_VOICE` and `MICHI_REPO_ROOT` are optional overrides.
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

### Both receptionists are Groq; only delegated roles are Claude

`main.py` builds `GroqLanguageModel` (`--model`, default `openai/gpt-oss-120b`)
for conversation and routing — one HTTP call per turn, no `claude_agent_sdk`
subprocess spawn. Claude is reserved for delegated finance work
(`--role-model`, default `claude-sonnet-5`), which needs real Bash/Read/Write
tool use. The root `CLAUDE.md` and `README.md` may still describe this split as
*Mazda's* divergence; it is both receptionists' arrangement.

`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` must stay unset (local Claude Code
OAuth, not metered API) — same repo-wide rule; `finance_team/adapters/tool_agent.py`
holds the per-subprocess `env=` backstop.

### The composition root and the one cross-team import

`main.py` is the only module here allowed to name a concrete implementation.
The deliberate cross-team edge is `adapters/tool_agent_runner.py` importing
`finance_team.adapters.tool_agent.run_role` and `finance_team.roles`
(`ROLES`, `REPO_ROOT`) — Toyota's whole purpose. It lives in the *runner* and
not in `routing_model.py` on purpose: that is what lets Mazda reuse the same
`RoutingModel` without pulling the Claude SDK and the `rol_finances` repo root
in behind her. `roles.py` imports `finance_team.roles.ROLES` for names only,
nothing SDK- or DB-shaped.

### A profile is the unit of "a receptionist"

`ReceptionistProfile` (frozen dataclass, pure data — role config, not a wire
contract, same reasoning as `finance_team`'s `AgentRole`) carries the
`agents/<name>/` folder name, display name, default session, nexus subdir,
system prompt, roster, a `build_runner` factory, the peer session/label, the
TTS voice, and two flags — `handoff` and `operations` — for the dashboard-only
decorators Toyota has and Mazda doesn't.

`build_receptionist(profile, settings, role_model, ...)` is the whole
assembly; `build_toyota()` survives only as a thin wrapper because
`turn_runner.py` and four tests name it. **Adding a third receptionist is one
`PROFILES` entry plus an `agents/<name>/` folder** — not another `build_*()`
and emphatically not another package.

The two runner factories in `profiles.py` import their adapters *inside the
function*. Keep it that way: module-scope imports there would drag
`finance_team` (hence `claude_agent_sdk`) and `GroqLanguageModel` onto the
path of anything that merely reads the profiles, including Mazda's offline
tests.

### Delegation is a Strategy, so an unwired agent can say so

`delegation.py` holds `IDelegationRunner` —
`run(role, agent_name, model, extra="") -> AgentReply`. `RoutingModel` matches
the control line, speaks the acknowledgment, and hands the role to the
injected runner; it never learns whether anything real is on the other side.

| runner | used by | what it does |
|---|---|---|
| `ToolAgentRunner` (`adapters/`) | Toyota | `run_role` through the Claude Agent SDK against `rol_finances` |
| `MichiRunner` (`adapters/`) | Mazda's `michi` | collects the progress snapshot, then one constrained model call |
| `UnwiredRunner` | Mazda's other five | says plainly the connection isn't built and nothing ran |
| `RunnerMap` | Mazda | dispatches by agent name over a default |

`UnwiredRunner` is load-bearing, not a placeholder to be "finished":
**Mazda's standing rule is never to claim work was done unless a tool
confirmed it**, so a stub that fabricated a plausible result would be the one
thing she must not do. Her five stub `AgentRole`s also carry
`allowed_tools=[]` so nothing can run against real financial data by accident,
and `agents/mazda/tools/*/tool.json` are declared `"kind": "stub"` — the
`db_query` one is read-only by design.

Her prompt also has a vocabulary rule, pinned by a test: she must not use the
words "idempotent", "fail-closed" or "canonical" — say "safe to repeat",
"stops safely rather than guessing", "standard vendor key".

### Michi is deterministic first, explained second

`progress/tracker.py` reads
`<repo_root>/finance_reports/<year>/<month>/<account>.json` with no model
involved and persists one snapshot; `narrate.py` turns that snapshot into a
prompt; only then does a model speak. That order is the design — the
explanation cannot add a fact the tracker didn't collect, and
`tests/test_michi_routing.py` pins it. Repo root defaults to
`receptionist_team/workspace/` and `MICHI_REPO_ROOT`,
`MICHI_EXPECTED_ACCOUNTS`, `MICHI_MONTHS_BACK`, `MICHI_STALE_DAYS` override the
defaults (they were `MAZDA_TEAM_*` before the move; nothing in the repo sets
either spelling).

`tracker.REPORT_WRITER_AGENT` is `"michi"`: writing the finance reports is her
own job (it read `"sheeta"` until that `mazda_team` role was retired). She
carries no tools, so a next step assigned to her is work still waiting — never
work she did, and `narrate.py`'s system prompt says so explicitly, because a
tracker claiming she wrote a report is exactly the fabrication Mazda's rules
forbid. `TAX_FORM_AGENT` still reads `"tarita"`, a label naming an agent
outside this package rather than a routable target.

### Routing is a `LanguageModel` Decorator, and it is live

`RoutingModel` wraps the Groq model and satisfies the same `LanguageModel`
Protocol, so `ConversationAgent` sees one `complete()` and one `AgentReply`
and never learns a delegation happened (same shape as
`agent_memory/adapters/decorators.py`'s `RetryingModel`). Both the roster and
the runner are **injected** — `RoutingModel` has no hard-coded knowledge of
either receptionist's team.

```
ROUTE: <agent_name>
ROUTE: <agent_name> | <extra instructions>
```

matched with `re.fullmatch`, **not** `search` — ordinary conversation that
merely mentions routing must not fire a delegation. Keep it that way: for
Toyota a matched ROUTE line reaches `run_role(..., cwd=REPO_ROOT)` with
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

### The delegation acknowledgment

`TOYOTA_SYSTEM_PROMPT` tells the model to emit a bare ROUTE line because
"the application supplies the spoken acknowledgment separately so that it can
begin playback immediately while the delegated agent starts working." That is
now true on both front ends.

`RoutingModel.__init__` takes an optional `on_delegation: Callable[[str], None]`
and calls it with `roles.delegation_acknowledgment(role_name)` *before*
awaiting `run_role`. The sidecar passes one (see below), so a dashboard user
hears "let me check with accounting" within a second and the reply lands
minutes later on the same stream. `main.py`'s `repl()` wires
`AcknowledgmentSpeaker.say` into the same hook when `--voice` is set; typed
mode has no voice_io and correctly leaves `on_delegation=None`.
`tests/test_repl_voice_delegation.py` pins both branches and
`tests/test_delegation_acknowledgment.py` pins the ordering — the
acknowledgment reaches the sink *before* the slow call.

The callback is synchronous and fire-and-forget on purpose: it runs inside
`complete()`, so anything slow or raising there would delay or break the very
turn it is announcing. That is why `AcknowledgmentSpeaker`
(`adapters/voice_io.py`) plays on a daemon thread, swallows playback failures,
and returns immediately; `repl()` calls its `wait()` before speaking the real
reply so the two players never overlap.

### The sidecar

`service.py` is a `ThreadingHTTPServer` holding **one warm
`ConversationAgent`**, because loading the sentence-transformers model and the
LanceDB table costs ~30 seconds — per process, not per turn. Three endpoints:
`GET /health` (the dashboard's tile probe), `POST /turn`, which streams
NDJSON `service_models.TurnRecord`s: zero or more `acknowledgment`s, then a
`reply`, then a terminal `result` — or a single `error`; and `POST /compact`,
a single plain-JSON `{"ok": true, "session": ...}` reply for the dashboard's
"compact conversation" button. `/compact` rotates the warm agent onto a new
session id (`ConversationAgent.clear_context()`,
`agent_memory/agent.py`) rather than deleting anything — recall is
session-scoped, so the next turn's recall simply finds nothing from before.
It goes through `_TurnRunner`'s same lock as a turn, so it can't land
mid-turn, and `self._settings.session_id` is updated with it so `/health`
and the next `TurnRecord.session` agree.

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

### Every agent is a folder, Toyota included

`agents/<name>/` holds `system_prompt_template/`, `user_prompt_template/`,
`model_confiurations/` (spelled exactly that way on disk), `tools/` and
`documentation/`. Three modules consume it:

- `roles.py` renders `agents/toyota/system_prompt_template/system_prompt.md`,
  filling its `{{roster}}` and `{{allowed_agents}}` placeholders — the prompt
  text is no longer in `roles.py`, only the roster wording is;
- `main.py` builds `TemplatePromptComposer` from
  `agents/toyota/user_prompt_template/user_prompt.md` (byte-identical to
  `agent_memory.prompts.DEFAULT_TEMPLATE`, pinned by a test, so this is the
  prompt Toyota always had — now editable without touching Python);
- `extra_roles.py` turns every name in `EXTRA_ROLE_NAMES` into a
  `finance_team` `AgentRole` via `agents.AgentRoleFactory`, which is where
  `spreadsheet_reshaper`'s prompts went.

`agents/` imports `IApplication`, `IArgumentParser`, `ILifecycleLog` and
`LifecycleLogged` from `../../sssf_interfaces` — the SSSF factory's own
`adws/adw_interface_prompt.py`, loaded by file path — rather than restating
them. `agents/README.md` has the module-by-module responsibility table, the
placeholder rules, and what is deliberately left as a stub
(`documentation/`, the `tools/*` declarations, `toyota_mechanic/`).

### Memory

Standard `agent_memory` wiring, with two choices made in `main.py`:
`--embedding-profile` defaults to `sentence` (384-dim, local semantic model,
table `conversation_memory_sentence384`) because real human chat needs
meaning-based recall, and `Settings` is `model_copy`'d per profile so the
write-only archive lands in `nexus/<profile.nexus_subdir>/`.

Both receptionists share **one** LanceDB table, and each profile names the
other as its `peer_session`, so `PeerSessionRecall` lets each hear what the
user told the other (`tests/test_shared_memory_wiring.py`). The recall chain is
`SimilarityRecall → RecencyFloorRecall → PeerSessionRecall → TokenBudgetedRecall`.
Recall still reads LanceDB only — don't reintroduce a filesystem read into
that path.

## Style and housekeeping

- **This package does not follow the workspace bracket-spacing convention**
  (`str )`, `[ 0.0 ]`) — **except everything under `agents/`, which does.**
  That subtree is written to the SSSF template's rules on purpose; `main.py`,
  `profiles.py`, `rosters.py`, `delegation.py`, `roles.py`, `mazda_roles.py`,
  `extra_roles.py`, `service.py`, `adapters/`, `operations/`, `progress/` and
  `tests/` keep their unspaced style. Match the file you're editing; never normalize across
  the boundary.
- The convention **cannot** reach inside an f-string format specifier:
  `f"{ name:<24 }"` raises `ValueError: Unknown format code`. Write
  `f"{ name:<24}"`. `adw_interface_prompt.py`'s own `PrintLifecycleLog` and
  `ConsoleLifecycleLog` have exactly this bug (`#{ id( obj ):x }`) and raise
  on first call, which is why `agents/lifecycle.py` implements `ILifecycleLog`
  locally instead of importing them.
- `voice_metrics.log` is **tracked in git** and appended to by every voice
  run, so `--voice` always dirties the worktree. Don't commit it as part of
  an unrelated change.
- `adapters/voice_io.py` imports `log_stage` but only uses `Stopwatch` —
  harmless, but don't take the import as evidence of a second logging path.
- `workspace/progress/michi_state.json` is gitignored; `workspace/` itself is
  tracked for its `README.md` only.

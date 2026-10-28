# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope of this file

`lancedb_memory/` is one directory of the `agent_blocks` workspace. Three
documents overlap here; read them in this order and prefer the more specific
one on conflict:

1. `../CLAUDE.md` — workspace-wide architecture and the bracket-spacing style rule.
2. `lancedb_memory/README.md` — the long-form narrative: patterns table, per-team
   feature docs, design rationale.
3. this file — the commands, the invariants that are easy to break, and the
   places where the two documents above have drifted from the code.
4. `receptionist_team/CLAUDE.md` — authoritative for both receptionists.

## Environment

Everything runs through `lancedb_memory/.venv` (Python 3.14, `pydantic`,
`lancedb`, `pyarrow`, `claude-agent-sdk`, `openai`, `sentence-transformers`,
plus `pymysql`, which is there only because
`adws/adw_interface_live_finance.py` reads the live `nonprofit_finance`
schema, and `python-dotenv`/`pyyaml`/`rich`, which are there only because
`sssf_interfaces` loads the SSSF factory's `adw_modules`, and `selenium`,
which is there only because `adws/adw_interface_search_open.py` reads a
results page — this venv is the one interpreter that has both it and the
ADW's `pyyaml`/`rich`/`dotenv`, which is why that workflow runs under
`.venv/bin/python` rather than its own `uv run` header).
The system interpreter is PEP 668 externally-managed, so **always invoke
`.venv/bin/python`, never bare `python3`**. There is no `requirements.txt`,
`pyproject.toml`, or lockfile anywhere in the workspace — dependencies were
installed ad hoc, so `pip list` in the venv is the only manifest.

Restored on 2026-10-28 with Python 3.14.7. To recreate or top up this same
environment (the recorder also needs `aiohttp`, tests need `pytest`):

```bash
# From the repository root; create only if .venv is missing.
uv venv --python 3.14 lancedb_memory/.venv
uv pip install --python lancedb_memory/.venv/bin/python pydantic lancedb pyarrow claude-agent-sdk openai pymysql python-dotenv pyyaml rich selenium aiohttp pytest
# Large install: obtain user approval first. CPU wheels avoid CUDA downloads.
uv pip install --python lancedb_memory/.venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
uv pip install --python lancedb_memory/.venv/bin/python sentence-transformers --index https://download.pytorch.org/whl/cpu --index-strategy first-index
```

`ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` must be **unset** so the Agent SDK
uses local Claude Code subscription auth instead of metered API billing. Both
Claude adapters additionally pass `env={ "ANTHROPIC_API_KEY": "",
"ANTHROPIC_BASE_URL": "" }` in `ClaudeAgentOptions` as a per-subprocess
backstop — a live `export` in `~/.bashrc` on this machine silently flips
billing, and only surfaces as "Credit balance is too low" once that key drains.
**Do not remove those `env=` lines**; see the comment in
`agent_memory/adapters/claude_model.py` for the full story.

## Commands

All commands run from `lancedb_memory/` (the import root for every package here).

```bash
# agent_memory — the base engine's own REPL
.venv/bin/python main.py --session default
.venv/bin/python main.py --no-persist                      # InMemoryMemoryStore
.venv/bin/python main.py --embedding-profile sentence       # hash | sentence | openai

# finance_team — headless map-reduce over the rol_finances MySQL DB
.venv/bin/python -m finance_team.orchestrate
.venv/bin/python -m finance_team.orchestrate --only mapper_bank
.venv/bin/python -m finance_team.orchestrate --skip-receipts --skip-expense-check

# receptionist_team — Toyota and Mazda (see receptionist_team/CLAUDE.md)
.venv/bin/python -m receptionist_team.main                   # Toyota
.venv/bin/python -m receptionist_team.main --voice --session toyota
.venv/bin/python -m receptionist_team.main --agent mazda     # Mazda, same engine
.venv/bin/python -m receptionist_team.progress.cli           # Mickey snapshot, no LLM
.venv/bin/python -m receptionist_team.agents.cli list        # the agent folders, no LLM
.venv/bin/python -m receptionist_team.agents.cli show mazda
.venv/bin/python -m receptionist_team.agents.cli validate
```

### Tests

**There is no pytest in the venv and no pytest config.** Every test file is a
standalone script; "running a single test" means running its file, or calling
its `test_*` function from a REPL.

```bash
.venv/bin/python tests/test_agent.py                # turn loop; no network, no disk
.venv/bin/python tests/test_lancedb_store.py        # real LanceDB in a tmpdir
.venv/bin/python tests/test_embedding_profiles.py   # factory; asserts torch is NOT imported
.venv/bin/python tests/test_fallback_model.py       # FallbackModel; no provider contacted

.venv/bin/python -m receptionist_team.tests.test_mazda_profile
.venv/bin/python -m receptionist_team.tests.test_mickey_routing
.venv/bin/python -m receptionist_team.tests.test_progress_tracker
.venv/bin/python -m receptionist_team.tests.test_delegation_acknowledgment
.venv/bin/python -m receptionist_team.tests.test_status_route
.venv/bin/python -m receptionist_team.tests.test_adw_browser_runner
.venv/bin/python -m receptionist_team.tests.test_search_open_adw
.venv/bin/python -m receptionist_team.tests.test_figure_route
.venv/bin/python -m receptionist_team.tests.test_live_finance_adw
.venv/bin/python -m receptionist_team.tests.test_live_finance_runner
.venv/bin/python -m receptionist_team.tests.test_post_office_runner

.venv/bin/python -m receptionist_team.agents.tests.test_agent_catalog

.venv/bin/python -m agent_comms.tests.test_post_office   # the post office; no model, no network
.venv/bin/python -m agent_comms.tests.test_operations    # every operation has its adws/adw_op_*.py
```

`agent_comms/` is the post office every agent's work goes through -- Toyota,
Sydney, Sonnet, Opus and the mechanic. Read `agent_comms/README.md` first.
Its rules: `factory.py` is the only module naming concretes; agents are
cards in `agents.json`, not code; operations are one responsibility each and
each one is also an ADW (`adws/adw_op_<name>.py`, via `adws/post_office_adw.py`).
Runtime data lands in the gitignored `adws/adw_data/post_office/`
(`AGENT_POST_OFFICE_ROOT` overrides it -- use that for scratch runs).

`tests/*.py` self-bootstrap via `sys.path.insert(...parent)`, so they also work
when invoked by path. `tests/runner.py` is a ~30-line async harness that
discovers module-level `test_*` coroutines — every team's tests call it from
their `__main__` block, which is why they must be run as `-m` modules from
`lancedb_memory/`. Shared fakes (`ScriptedModel`, `RecordingObserver`,
`TickingClock`) live in `lancedb_memory/tests/fakes.py` rather than being
duplicated per team. All tests are offline and need no API key.

`finance_team` has **no tests** — it talks to a real MySQL DB.
`receptionist_team` has tests for its sidecar turn loop, its `agents/` folder
catalog, both profiles, the delegation acknowledgment and Mickey's tracker;
what sits between the sidecar and the engine still needs the real dashboard.

## Architecture

### Agents are folders, and the SSSF vocabulary is a module

`sssf_interfaces/` is a one-responsibility module: locate
`super-simple-software-factory/.claude/skills/sssf/templates/adws/adw_interface_prompt.py`
(override with `SSSF_ADWS_DIR`), load it by file path, and re-export its roles
— `IApplication`, `IArgumentParser`, `IPhase`, `IPhaseExecutor`,
`ILifecycleLog`, `LifecycleLogged`. It is a `uv run` script with a PEP 723
header, not a package, so a plain `import` cannot reach it; failure is loud
and names `SSSF_ADWS_DIR` rather than falling back to a local copy of the
interfaces, because a silent fallback is exactly how the two definitions would
drift. This is the standardization direction for the workspace — new
interface-shaped code should import the vocabulary, not restate it.

`receptionist_team/agents/` is the first consumer: one folder per agent
(both receptionists included) holding `system_prompt_template/`,
`user_prompt_template/`, `model_confiurations/` (sic), `tools/` and
`documentation/`, with a Protocol-per-responsibility loader over it.
`receptionist_team/agents/README.md` is authoritative; the
`/new-receptionist-agent` skill is the procedure for adding one. Mazda's
specialists are not folders yet — they are stub `AgentRole`s in
`mazda_roles.py`, and her three `tools/*/tool.json` files are declared
`"kind": "stub"`.

Two gotchas worth knowing before you copy the template's style: the
bracket-spacing convention cannot reach inside an f-string format specifier
(`f"{ x:<24 }"` raises `ValueError`), and the template's own
`PrintLifecycleLog`/`ConsoleLifecycleLog` ship with that bug
(`#{ id( obj ):x }`) so they raise on first call —
`receptionist_team/agents/lifecycle.py` implements `ILifecycleLog` locally for
that reason.

### One engine, three composition roots — and two receptionists in one of them

`agent_memory/` is a reusable engine; `main.py`, `finance_team/` and
`receptionist_team/` are three independent composition roots over it.
`mazda_team/` was a fourth until Mazda became a profile inside
`receptionist_team` (see below); it is gone, and nothing should re-create it.

The dependency direction is strictly one-way: teams import `agent_memory`,
never the reverse, and the teams don't import each other except for one
deliberate edge — `receptionist_team/adapters/tool_agent_runner.py` imports
`finance_team.adapters.tool_agent` and `finance_team.roles`, because Toyota's
whole purpose is to delegate to finance roles. That import lives in the runner
and **not** in `routing_model.py` on purpose: it is what lets Mazda reuse the
same `RoutingModel` without dragging the Claude SDK and the `rol_finances`
repo root in behind her.

### Two receptionists, one `build_receptionist()`

`profiles.py` holds a frozen `ReceptionistProfile` per receptionist — agents/
folder, display name, default session, nexus subdir, system prompt, roster,
runner factory, peer session/label, TTS voice, and two flags for the
dashboard-only decorators (`handoff`, `operations`). `main.py
--agent {toyota,mazda}` picks one and `build_receptionist(profile, ...)`
assembles it; `build_toyota()` remains as a thin wrapper because
`turn_runner.py` and four tests name it.

Adding a third receptionist is one `PROFILES` entry plus an `agents/<name>/`
folder. It is not another `build_*()` function, and it is **not** a new
package — that was the mistake `mazda_team` embodied.

### Delegation is a Strategy, so an unwired agent can say so

`delegation.py`'s `IDelegationRunner` is how a matched ROUTE line actually
runs. `RoutingModel` matches the control line, speaks the acknowledgment, and
hands the role to the injected runner — it has no idea whether anything real
is on the other side:

| runner | used by | what it does |
|---|---|---|
| `ToolAgentRunner` (`adapters/`) | Toyota | `run_role` through the Claude Agent SDK against `rol_finances` |
| `MickeyRunner` (`adapters/`) | Mazda's `mickey` | collects the progress snapshot, then one constrained model call |
| `UnwiredRunner` | Mazda's other five | says plainly that the connection isn't built and nothing ran |
| `AdwBrowserRunner` (`adapters/`) | Mazda's `browser_opener` | subprocesses `adws/adw_interface_browser_open.py` and reports the line it wrote, verbatim |
| `AdwLiveFinanceRunner` (`adapters/`) | Toyota's `live_books` | subprocesses `adws/adw_interface_live_finance.py` and reports the figure it wrote, verbatim |
| `ToolAgentRunner` | Toyota's `search_link_opener` | an agent folder, not a dedicated runner, because picking which result *is* the site is a judgement |
| `RunnerMap` | Mazda | dispatches by agent name, with one default |

Toyota's two browser agents split on one question: does the user already have
the address? If yes it is `browser_opener`. If the site has to be *found*
first — "pull up Fifth Third Bank so I can log in" — it is
`search_link_opener`, which runs `adws/adw_interface_search_open.py`: search
headlessly, read the organic result links, pick the one that IS the site, trim
it to the front door, and only then spawn a real window the user can log in
by hand. The headless half is selenium-headless's `WebBrowserManager`
(`/home/adamsl/selenium-headless/browser_manager/`), imported by file path
inside that ADW's `SeleniumResultPageReader` and nowhere else; the
window-opening half is `adw_modules/map_open.py` unchanged. Bing leads its
backend ladder because Google and DuckDuckGo both answer headless Chrome with
a challenge page — measured, not preferred, and pinned by a test. When no
result can be picked it opens the *search* page and says so; it never invents
a destination, the same rule `UnwiredRunner` enforces for Mazda. See
`receptionist_team/CLAUDE.md` for the rest.

`UnwiredRunner` exists because Mazda's standing rule is never to claim work
was done without a tool confirming it. **Do not replace it with a stub that
fabricates a plausible result** — that is the one thing she must not do.

### Mickey is deterministic first, explained second

`receptionist_team/progress/` (ported intact when `mazda_team` was retired):
`tracker.py` reads `<repo_root>/finance_reports/<year>/<month>/<account>.json`
with no model involved and writes one snapshot; `narrate.py` turns that
snapshot into a prompt; only then does a model speak. That order is the whole
design — the explanation cannot add a fact the tracker didn't collect. Repo
root defaults to `receptionist_team/workspace/`; `MICKEY_REPO_ROOT`,
`MICKEY_EXPECTED_ACCOUNTS`, `MICKEY_MONTHS_BACK`, `MICKEY_STALE_DAYS` override
the defaults (they were `MAZDA_TEAM_*` before the move, and nothing in the
repo sets either spelling). `tracker.REPORT_WRITER_AGENT` is `"mickey"` —
writing the reports is her own job now (it was `"sheeta"`, a retired
`mazda_team` role). She has no tools, so a next step assigned to her is work
still waiting, and `narrate.py`'s prompt forbids her from claiming she wrote
anything. `TAX_FORM_AGENT` is still `"tarita"`: a label in the snapshot
naming an agent outside this package, not something routable.

### Toyota reads the live books, and only reads them

`adws/adw_interface_live_finance.py` is Toyota's one path to a real number:
five phases, no model anywhere in it, against the live `nonprofit_finance`
MySQL schema. It exists because a prompt rule cannot supply a figure — her
only data-shaped assignment was synthetic, so asked what the organization had
spent she produced a believable amount, which recall then handed back to her
as history.

Read-only is enforced in **code**, not by grants: the MySQL account on this box
holds ALL PRIVILEGES, so `ReadOnlyBooks` (a Decorator over `ILiveBooks`)
refuses anything that is not a single `SELECT`/`WITH`, and the session is
additionally opened `READ ONLY` and never committed. Both layers are
deliberate; neither is redundant. The organization is resolved by
`organizations.type = 'NONPROFIT'` each run rather than by a pinned `org_id`,
because the same schema also holds two sets of *personal* books. The column is
`type`, a reserved word, **not** `org_type`.

An unmatched question answers nothing — there is no default query. The
installed questions are a Strategy registry (`install_questions()`, most
specific first, `total_spend` last among the amount questions), so adding one
is a single entry there. `--list-questions` touches no database. Credentials
come from `NON_PROFIT_*`/`DB_HOST`/`DB_PORT` in the environment or from
`--env-file` (default `/home/adamsl/.config/mazda/finance-db.env`); the
environment wins — and note that `finance_team/roles.py` briefs
`/home/adamsl/planner/.env` for its own DB work, a file that **does not
exist**. See `receptionist_team/CLAUDE.md` for the rest.

### The DIP boundary is a greppable import rule

`agent_memory/agent.py` may import `.models` and `.ports` **and nothing else**.
No `lancedb`, no `claude_agent_sdk`, no `pathlib`. Concrete third-party imports
appear only under `agent_memory/adapters/`; each composition root's `main.py` /
`orchestrate.py` is the only module there allowed to name a concrete class. If
an import line grows in `agent.py`, the design has regressed — that single
`grep` is the enforcement mechanism, since there's no linter.

`agent_memory/ports.py` holds all seven Protocols (`Embedder`, `MemoryStore`,
`RecallStrategy`, `PromptComposer`, `LanguageModel`, `TurnObserver`, `Clock`).
Role definitions (`finance_team/roles.py`, `receptionist_team/mazda_roles.py`)
follow the same posture: pure data `AgentRole` dataclasses, no SDK import — and
so do `receptionist_team/profiles.py` and `rosters.py`.

> Naming note: the user prefers interface names like `ISomeUsefulInterface` over
> a `*Port` suffix. The existing Protocols here are unsuffixed (`Embedder`, not
> `EmbedderPort`) — match that, and don't introduce `*Port` names.

### The turn cycle, and why the order matters

`ConversationAgent.handle_turn()` is a Facade over: embed → **recall** →
compose → complete → persist → notify observers. Recall happens *before*
persistence so the current turn is never its own context. A test asserts this.

### LanceDB is the sole read path; nexus is write-only

The LanceDB row carries the message body, so recall never touches the
filesystem. `nexus/` is a write-only archive behind `NexusArchiveObserver`,
kept only so raw text outlives any particular vector store. This is the fix for
`3o_mini_chat_w_lance.py`'s dual-source-of-truth bug, where a missing nexus
file silently emptied the recalled conversation.
`tests/test_lancedb_store.py` asserts deleting the nexus entirely does not
affect recall — **do not reintroduce a filesystem read into the recall path.**

Each receptionist namespaces its archive by `model_copy`-ing `Settings` at
startup (`nexus/toyota/`, `nexus/mazda/`), so `nexus/` has both loose files
from `main.py` and per-agent subdirectories.

### Embedding profile ↔ table pairing is not a free choice

A vector's width is baked into it, so `adapters/embedding_profiles.py` is the
one place the `(embedder, table_name)` pair is decided:

| profile | embedder | dims | table |
|---|---|---|---|
| `hash` | `HashingEmbedder` — lexical bag-of-words, offline, free | 256 | `conversation_memory_hash256` |
| `sentence` | `SentenceTransformerEmbedder` — local semantic model | 384 | `conversation_memory_sentence384` |
| `openai` | `OpenAIEmbedder` — needs `OPENAI_API_KEY`, costs money | 1536 | `conversation_memory_openai1536` |

`PROFILE_BUILDERS` is a registry, not an `if/elif` — a fourth profile is one
new dict entry. The `sentence` and `openai` builders import their heavy module
*inside* the function (torch / network), and
`tests/test_embedding_profiles.py` asserts that selecting `hash` never imports
torch. `LanceDBMemoryStore._assert_dimensions_match` raises on a mismatch
rather than writing unsearchable rows. `lance_data/` currently holds only the
hash256 and sentence384 tables.

Defaults differ per entrypoint, deliberately: `main.py` and
`agent_memory/config.py` default to `hash` (fast, free, test-shaped); the
receptionists default to `sentence` (real human chats need meaning-based
recall).

### Routing is a `LanguageModel` Decorator

Both receptionists route via a `RoutingModel` that wraps the conversational
model and satisfies the same `LanguageModel` Protocol — so `ConversationAgent`
sees one `complete()` call and one `AgentReply` and never knows a delegation
happened (same shape as `adapters/decorators.py`'s `RetryingModel`). The system
prompt instructs the LLM to answer with a bare control line:

```
ROUTE: <agent_name>
ROUTE: <agent_name> | <extra instructions>
```

matched with `re.fullmatch`, **not** `search`, so ordinary conversation that
merely mentions routing can't trigger a delegation. An unknown agent name
yields a friendly message listing known agents, not a crash.

Two routes no longer wait for the model to agree. `delegation.IRouteOverride`
is consulted *before* the inner model is called. Mazda's is
`adapters/status_route.StatusQuestionRoute("mickey")`, because the 20b often
ignored her prompt's "every status question goes to Mickey" and answered from
recall instead — re-asserting a year of finance reports that do not exist.
Toyota's is `adapters/figure_route.FigureQuestionRoute("live_books")`, for the
same failure over money: with no way to read a real figure she answered from
recalled conversation and invented plausible amounts, and 66 stored training
turns kept handing the habit back as history. See
`receptionist_team/CLAUDE.md` for the details that matter: both read the
*last* `USER:` line (shared in `adapters/latest_user.py`), both force nothing
when the prompt has no marker, and Toyota's vetoes requests that belong to a
more capable agent.

`MemoryStore.forget(ids)` and `receptionist_team/forget.py` exist for the
aftermath of that episode: a wrong turn is stored history, so recall keeps
handing it back as fact, and deleting is the only way out of the loop.

### A spent Groq budget is no longer the end of the turn

`decorators.FallbackModel` is a third `LanguageModel` Decorator beside
`RetryingModel`: a list of backends tried in order, so the agent still sees
one `complete()` call. `RetryingModel` handles "wait a second and ask again";
this handles the kind of failure waiting cannot fix -- Groq's 200000 tokens
per model per day. `receptionist_team/main.py`'s `build_chat_model()` chains
Groq to `adapters/openai_model.py` (`gpt-5-mini`, with `reasoning_effort` pinned to the floor),
and builds the backup **only when a key is present** --
`RECEPTIONIST_OPENAI_API_KEY` in `receptionist_team/.env`, a scoped name so
`openai_embedder.py`'s bare `OpenAI()` cannot reach it -- so a machine without
a key behaves exactly as before. A third hop is one more list entry --
`ClaudeAgentModel` is already a `LanguageModel` and free on the local
subscription, just ~8s a turn against Groq's ~2s because of the CLI subprocess
spawn. See `receptionist_team/CLAUDE.md` for why the default is a mini and not
a nano.

### Chat model vs. tool model

Two Claude-facing adapters exist because the jobs differ:

- `agent_memory/adapters/claude_model.py` — `allowed_tools=[]`,
  `max_turns=1`, `setting_sources=[]`. Text-only chat, deliberately isolated:
  the SDK must not load the enclosing repo's `CLAUDE.md` or read files, because
  the agent's only context is what *our* store recalled.
- `finance_team/adapters/tool_agent.py` — tools enabled, `permission_mode="bypassPermissions"`, `max_turns=40`. These
  roles must query MySQL and walk receipt directories headless, with no human
  to approve each call. They also convert `ResultMessage.is_error` into a
  `RoleResult(is_error=True)` rather than treating an error string as output.

`finance_team/orchestrate.py` fans the four `mapper_*` roles (partitioned on the
pre-existing `expenses.method` enum, so they share no state) plus the two
receipt roles out via `asyncio.gather`, then runs `synthesizer` last because it
reads the four mappers' JSON files.

### The path-hardcoding exception

`agent_memory/config.py` is the only place paths are decided for the engine, and
every default lands inside this repo — do not reintroduce the legacy script's
`/home/adamsl/linuxBash/...` absolutes. `finance_team/roles.py` is the
deliberate exception: `REPO_ROOT = "/home/adamsl/rol_finances"` and a DB
briefing pointing at `/home/adamsl/planner/.env`, because those roles operate on
a real external repository. Mickey keeps her own escape hatch
(`MICKEY_REPO_ROOT`, default `receptionist_team/workspace/`).

## Known drift between docs and code

Correct these if you touch the surrounding area; don't trust the docs over the
source.

- **`README.md`'s Layout block is stale.** It lists `models.py` and
  `observers.py`; both are actually packages (`agent_memory/models/`,
  `agent_memory/observers/`) with one class per module, re-exported from
  `__init__.py`. Import sites are unaffected (`from agent_memory.models import
  Turn` still works) — the layout description is what's wrong.
- **Toyota no longer runs on Claude.** `../CLAUDE.md` and `README.md` describe
  the Groq-for-conversation / Claude-for-tools split as *Mazda's* divergence,
  but `receptionist_team/main.py` now builds `GroqLanguageModel` too
  (`--model`, default `openai/gpt-oss-120b`) and reserves Claude for delegated
  roles (`--role-model`, default `claude-sonnet-5`). Both receptionists share
  the split; only the TTS voice and nexus subdirectory still differ.
- **`prompt_response.txt` does not exist.** `Settings.prompt_template_path`
  points at it and `main.py` calls `TemplatePromptComposer.from_file(...)`, but
  `from_file` silently returns the built-in `DEFAULT_TEMPLATE` when the path is
  missing. So `main.py` works, and the on-disk template it advertises is not
  actually in play. Creating that file would silently change the prompt.
- **Both receptionists load their own `.env`.** There's no `python-dotenv` in
  the venv, so `receptionist_team/main.py` parses `receptionist_team/.env` with
  `os.environ.setdefault` for whichever receptionist `--agent` selected.
  `GROQ_API_KEY` is required; `PIPECAT_PILOT_AGENT_ID` is optional. Don't add a
  `source .env` step to docs — the code already handles it, and `setdefault`
  means an already-exported value wins.
- **`mazda_team/` is gone.** Mazda is a `ReceptionistProfile` in
  `receptionist_team/profiles.py`, reached with `--agent mazda`. Any surviving
  reference to `mazda_team`, `MAZDA_TEAM_REPO_ROOT`, `specialist_roles.py`, or
  the `sheeta`/`tarita` roles in docs, specs or skills is stale.

## Style

The workspace bracket-spacing convention (`str )`, `[ 0.0 ]`, tight stacked
`])`) is applied **per file, inconsistently**, and that's intentional:

- follows it: `main.py`, `3o_mini_chat_w_lance.py`, all of `agent_memory/`,
  `tests/`, `finance_team/orchestrate.py`, `finance_team/adapters/tool_agent.py`
- does not: `receptionist_team/*` **except** `receptionist_team/agents/*`,
  which follows it — that includes `profiles.py`, `rosters.py`,
  `delegation.py`, `mazda_roles.py`, `adapters/*`, `progress/*` and `tests/*`

Match the file you're editing. Never run a normalizing pass across both groups.

Pydantic `BaseModel` is mandatory for data crossing a boundary
(`agent_memory/models/`). Internal non-serialisable carriers stay plain —
`EmbedderBundle` is a `NamedTuple` because it holds a live `Embedder` instance,
and `AgentRole` is a frozen dataclass because it's role config, not a wire
contract. Both carry a comment saying so.

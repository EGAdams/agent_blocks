# Plan: `mazda_team` — Mazda (receptionist) + Sheeta + TaRita

## Goal

Replicate the `receptionist_team` + `finance_team` pattern in one new,
self-contained directory: `lancedb_memory/mazda_team/`. It holds:

- **Mazda** — a conversational receptionist (Toyota's counterpart), with a
  **different edge-tts voice than Toyota's**.
- **Sheeta** — a "create spreadsheet" agent (team lead for future
  spreadsheet-creation agents; does the work directly for now).
- **TaRita** — an agent that extracts tax data and writes it into IRS tax
  form fields.

`mazda_team` does **not** import from `finance_team` or `receptionist_team`.
It is a self-contained sibling that copies and adapts their pattern, the same
way `finance_team` and `receptionist_team` are each self-contained (except
for the one existing, intentional coupling: `receptionist_team` already
imports `finance_team.roles`/`finance_team.adapters.tool_agent` because
Toyota specifically routes to finance_team — that coupling is not being
replicated here; Mazda routes to her own `mazda_team.specialist_roles`).

## Important naming collision — do not touch

`/home/adamsl/agent_blocks/mazda/` **already exists** and is a large,
unrelated in-progress migration (`finance_orchestrator`, `agent_engine`,
`MIGRATION_PLAN.md`, etc. — a different "Mazda" system that receives/routes
finance documents for `rol_finances`). It has nothing to do with this task.

Do not read into it further, do not edit anything under it, and do not let
its existence change any path in this plan. The new work lives entirely under
`/home/adamsl/agent_blocks/lancedb_memory/mazda_team/`, which is a different
path with no overlap. Flag this collision to the user in your build summary
so they're aware two different "Mazda" things now exist in this repo.

## Reference material (read, don't copy blindly)

- `lancedb_memory/receptionist_team/` (Toyota): `roles.py`, `main.py`,
  `adapters/routing_model.py`, `adapters/voice_io.py`, `adapters/metrics.py`,
  `.env`, `__init__.py`.
- `lancedb_memory/finance_team/`: `roles.py`, `orchestrate.py`,
  `adapters/tool_agent.py`, `__init__.py`.
- `lancedb_memory/README.md` — has full prose sections for both existing
  teams; add a matching section for `mazda_team`.
- Voice: `/home/adamsl/letta-code/dashboard/http_app/post_routes.py` —
  `/api/tts` accepts `{"text": ..., "voice": ...}`; `voice` is optional and
  server-side default is `en-GB-SoniaNeural` (`dashboard/server.py`,
  `EDGE_TTS_VOICE` env, defaulting to that id) — this is the voice Toyota
  gets today because `receptionist_team/adapters/voice_io.py` never sends a
  `voice` key. The allowed voice catalogue is
  `agents/registry.py:VOICE_OPTION_SPECS` in that same dashboard repo (e.g.
  `en-US-AriaNeural`, `en-US-JennyNeural`, `en-US-AnaNeural`, ...). Any id
  from that list, other than `en-GB-SoniaNeural`, satisfies "a different
  voice than Toyota has now."

## Style note

`finance_team`'s files (`roles.py`, `orchestrate.py`,
`adapters/tool_agent.py`) use the repo's unusual padded-bracket style
(`AgentRole( name=...)`, `dict[ str, str ]`, `def f( x: int ) -> int:`).
`receptionist_team`'s files (`roles.py`, `main.py`,
`adapters/routing_model.py`, `adapters/voice_io.py`, `adapters/metrics.py`)
use ordinary PEP8 spacing. New files should match the spacing style of
whichever existing file they most directly mirror (see file list below) —
this is new code, so match don't invent, but don't mix styles within one
file, and don't "fix" the style of a file whose content you copied from a
padded-bracket source.

## Files to create

All new, under `lancedb_memory/mazda_team/`:

1. **`__init__.py`** — module docstring describing the team: Mazda the
   receptionist, Sheeta (spreadsheet lead) and TaRita (tax-form extraction),
   mirroring `finance_team/__init__.py`'s docstring style and pointing at
   `specialist_roles.py` (agent briefs) / `orchestrate.py` (composition
   root) / `main.py` (Mazda's REPL) the way that file points at `roles.py`/
   `orchestrate.py`.

2. **`specialist_roles.py`** — mirrors `finance_team/roles.py`'s *shape*
   (padded-bracket style), but far simpler, no MySQL/DB briefing:
   - `AgentRole` dataclass: same fields as `finance_team.roles.AgentRole`
     (`name`, `output_path`, `system_prompt`, `task_prompt`, `allowed_tools`
     default `["Bash", "Read", "Write", "Glob", "Grep"]`, `max_turns` default
     `40`).
   - `REPO_ROOT = os.environ.get("MAZDA_TEAM_REPO_ROOT", str(Path(__file__).resolve().parent / "workspace"))`
     — self-contained default (a `workspace/` folder created on first run by
     the agent itself, same "create the directory if it doesn't exist"
     convention `finance_team.roles` uses for `REPORTS_DIR`), overridable
     once a real spreadsheet/tax repo exists to point at instead. Document
     this env var in the module docstring as a placeholder — there is no
     existing spreadsheet or tax-form repo on this machine to wire up yet.
   - `ROLES["sheeta"]`: `AgentRole(name="sheeta", output_path=f"{REPO_ROOT}/spreadsheets/sample_spreadsheet.csv", ...)`.
     - `system_prompt`: she is the lead/responsible agent for a (currently
       one-agent) team that creates spreadsheets; state plainly that she
       does the work herself today and more teammates may be added later.
     - `task_prompt`: create `{REPO_ROOT}/spreadsheets/` if missing, write
       one example CSV (plain stdlib-writable — **no** `openpyxl`/`pandas`
       in this venv, confirmed: `.venv/lib/python3.14/site-packages` has
       neither installed, so don't ask for `.xlsx` yet) with a small,
       clearly-labeled sample table (e.g. columns `item, quantity, unit_price,
       total`), then reply with one sentence naming the file and row count.
   - `ROLES["tarita"]`: `AgentRole(name="tarita", output_path=f"{REPO_ROOT}/tax_forms/sample_form_1040.json", ...)`.
     - `system_prompt`: she extracts tax data from source material and maps
       it into IRS tax form fields; note there is no live intake source
       wired up yet, so for a bare run she should look for input under
       `{REPO_ROOT}/tax_intake/` (create it if missing) and if it's empty,
       produce a clearly-labeled placeholder/template output plus a note in
       her reply that no intake data was found, rather than inventing tax
       figures.
     - `task_prompt`: check `{REPO_ROOT}/tax_intake/` for any files; if
       present, extract whatever tax-relevant fields are legible and map
       them into a Form 1040-shaped JSON object (field name -> value,
       using plain-language IRS field names like `filing_status`,
       `wages_line_1a`, `total_income_line_9`, etc. — she should use her own
       judgment for exact field naming since no real form schema is wired up
       yet); if absent, write a template JSON with the same field names and
       null values. Reply with one sentence: whether intake data was found
       and how many fields were populated vs. templated.
   - Keep both task prompts explicitly scoped to "no live DB, no rol_finances
     access" — unlike `finance_team`, nothing here touches
     `/home/adamsl/rol_finances`.

3. **`roles.py`** — Mazda's persona, mirrors `receptionist_team/roles.py`
   (ordinary spacing):
   - `from .specialist_roles import ROLES`
   - `ROLE_BLURBS`, `ROLE_SPOKEN_NAMES`, `ROLE_START_TASKS` dicts, one entry
     each for `"sheeta"` and `"tarita"` (e.g. blurb: "leads spreadsheet
     creation" / "extracts tax data into IRS form fields"; spoken name: "our
     spreadsheet specialist" / "our tax form specialist").
   - Same completeness-check loop `receptionist_team/roles.py` has (raises
     `RuntimeError` if any `ROLES` name is missing from one of the three
     dicts) — copy that pattern so a future third role can't silently ship
     without a blurb.
   - `delegation_acknowledgment()` — same shape/docstring as
     `receptionist_team.roles.delegation_acknowledgment`, raising
     `ValueError` for an unknown agent name.
   - `MAZDA_SYSTEM_PROMPT` — adapt `TOYOTA_SYSTEM_PROMPT` wording: name is
     Mazda, team reference lists sheeta/tarita instead of the finance roles,
     same spoken-reply style rules, same `ROUTE: <agent_name>` /
     `ROUTE: <agent_name> | <extra instructions>` protocol, same "ask for
     clarification unless >90% sure" rule, same rule against inventing
     agent results.

4. **`main.py`** — mirrors `receptionist_team/main.py` (ordinary spacing),
   adapted:
   - Same `_load_dotenv()` helper (loads `mazda_team/.env`).
   - `build_mazda(settings, role_model)` — identical wiring to
     `build_toyota`: `build_embedder`, `LanceDBMemoryStore`,
     `TokenBudgetedRecall(SimilarityRecall(...))`, `CostObserver`,
     `GroqLanguageModel(model=settings.model, system_prompt=MAZDA_SYSTEM_PROMPT)`,
     wrapped in this package's `RoutingModel`, `ConversationAgent` with
     `CompositeObserver([ConsoleObserver(), cost, NexusArchiveObserver(settings.nexus_dir)])`.
   - `repl()` — identical shape, prints `"Mazda -- session: ... model: ... voice: ..."`.
   - `voice_io = MazdaVoiceIO() if voice else None` (see adapters/voice_io.py
     below) instead of bare `DashboardVoiceIO()` — pass the Mazda-specific
     edge-tts voice id in here.
   - `main()` argparse: `--session` default `"mazda"` (not `"toyota"` — keeps
     her LanceDB-recall session distinct from Toyota's), `--model` default
     `openai/gpt-oss-120b` (same Groq model Toyota uses is fine to reuse),
     `--role-model` default `claude-sonnet-5`, `--embedding-profile` same
     three choices, `--voice` flag. `nexus_dir` update:
     `settings.nexus_dir / "mazda"` (not `"toyota"`).

5. **`orchestrate.py`** — mirrors `finance_team/orchestrate.py` (padded
   spacing), simplified for 2 roles instead of 7:
   - `.venv/bin/python -m mazda_team.orchestrate` runs both roles
     concurrently via `asyncio.gather`; `--only sheeta` / `--only tarita`
     supported; `--model` argparse option. No mapper/synthesizer fan-in step
     needed (drop that part of finance_team's shape — there's no reduce
     step here yet).

6. **`.env`** — mirrors `receptionist_team/.env`'s two lines
   (`PIPECAT_PILOT_AGENT_ID`, `GROQ_API_KEY` — same values, they're
   machine/account-level, not Toyota-specific) plus one new line:
   `MAZDA_EDGE_TTS_VOICE=en-US-AriaNeural` with a one-line comment
   explaining it picks Mazda's edge-tts voice, distinct from Toyota's
   default `en-GB-SoniaNeural`. (Pick `en-US-AriaNeural` unless the builder
   has a stronger preference from `VOICE_OPTION_SPECS`; any different id
   from that catalogue works — just don't reuse `en-GB-SoniaNeural`.)

7. **`adapters/__init__.py`** — empty/minimal, mirrors
   `receptionist_team/adapters/__init__.py`.

8. **`adapters/tool_agent.py`** — copy `finance_team/adapters/tool_agent.py`
   verbatim in structure/behavior (padded spacing preserved), only editing
   the module docstring's first line to reference `mazda_team`'s roles
   instead of finance_team's, and the relative import
   `from ..specialist_roles import AgentRole` (instead of `from ..roles import AgentRole`).
   `RoleResult`, `run_role()`, the `ClaudeAgentOptions(... env={"ANTHROPIC_API_KEY": "", "ANTHROPIC_BASE_URL": ""} ...)`
   auth-forcing line, and the tool-call echo logic all stay as-is — this is
   generic infrastructure, not finance-specific.

9. **`adapters/routing_model.py`** — mirrors
   `receptionist_team/adapters/routing_model.py` (ordinary spacing):
   - Same `_ROUTE_RE` full-match regex.
   - `from ..specialist_roles import REPO_ROOT, ROLES` and
     `from .tool_agent import run_role` (local, not cross-team).
   - Same `RoutingModel.complete()` behavior: pass through ordinary replies,
     run the named role via `run_role(role, cwd=REPO_ROOT, model=self._role_model)`
     on a bare `ROUTE:` line, unknown-agent-name fallback text listing
     `sorted(ROLES)`.

10. **`adapters/voice_io.py`** — copy
    `receptionist_team/adapters/voice_io.py` and make one functional change:
    thread an explicit edge-tts voice id through to `/api/tts`, since that
    is the concrete mechanism that gives Mazda a different voice than
    Toyota's (Toyota's copy never sends a `voice` key at all, so she gets
    the dashboard's server-side default `en-GB-SoniaNeural`):
    - `DashboardVoiceIO.__init__(self, base_url=..., pipecat_agent_id=None, voice=None)`
      — store `self._voice = voice`.
    - In `_synthesize()`, change the request body from
      `json.dumps({"text": sentence})` to
      `json.dumps({"text": sentence, "voice": self._voice} if self._voice else {"text": sentence})`
      (don't send a `voice` key at all when `None`, so this class stays a
      drop-in replacement for Toyota's usage if ever reused elsewhere).
    - Everything else (silence-terminated recording via `parecord`, STT
      request to `/api/voice`, sentence-pipelined TTS playback via
      `ffplay`/`mpg123`/`paplay`, `VoiceUnavailableError` fail-closed
      behavior) stays unchanged — this is generic dashboard-voice-pipeline
      plumbing, not Toyota-specific, just relocated.
    - Update the module's `LOG_PATH`-adjacent doc comment in `metrics.py`
      (next file) rather than here; no path changes needed in this file
      itself since it doesn't reference `LOG_PATH` directly.

11. **`adapters/metrics.py`** — copy
    `receptionist_team/adapters/metrics.py` unchanged (`Stopwatch`,
    `log_stage`, `LOG_PATH = Path(__file__).resolve().parent.parent / "voice_metrics.log"`
    — the relative `parent.parent` already resolves correctly to
    `mazda_team/voice_metrics.log` once this file lives at
    `mazda_team/adapters/metrics.py`, no edit needed beyond the copy itself).

## README update

Add one new `## mazda_team -- Mazda, spreadsheet and tax teams` section to
`lancedb_memory/README.md`, placed after the existing `## receptionist_team`
section, matching that section's structure and depth:
- One paragraph: Mazda is a second, independent receptionist (same
  `ConversationAgent` engine, same Groq-for-chat / Claude-for-delegated-work
  split), routing to her own `mazda_team.specialist_roles` (`sheeta`,
  `tarita`) instead of `finance_team`'s roles.
- Table or short list of the two roles and what each writes today
  (`workspace/spreadsheets/sample_spreadsheet.csv`,
  `workspace/tax_forms/sample_form_1040.json`), explicitly marked as
  starter/placeholder outputs, not tied to a real spreadsheet or tax repo
  yet.
- One line stating she uses a different edge-tts voice
  (`MAZDA_EDGE_TTS_VOICE`, default `en-US-AriaNeural`) than Toyota's
  (`en-GB-SoniaNeural`), and why (`adapters/voice_io.py` now threads an
  explicit `voice` id into the `/api/tts` request body).
- Run commands, mirroring the receptionist_team section:
  ```bash
  cd lancedb_memory
  .venv/bin/python -m mazda_team.main                  # typed chat
  .venv/bin/python -m mazda_team.main --voice           # mic in / spoken out
  .venv/bin/python -m mazda_team.orchestrate            # run sheeta + tarita directly
  ```
- One line noting the `/home/adamsl/agent_blocks/mazda/` naming collision
  (different, pre-existing, unrelated system) so a future reader isn't
  confused by two "Mazda"s in the same repo.

## Verification (no live network/API calls required)

Run from `lancedb_memory/`:

```bash
.venv/bin/python -m py_compile mazda_team/__init__.py mazda_team/specialist_roles.py \
  mazda_team/roles.py mazda_team/main.py mazda_team/orchestrate.py \
  mazda_team/adapters/__init__.py mazda_team/adapters/tool_agent.py \
  mazda_team/adapters/routing_model.py mazda_team/adapters/voice_io.py \
  mazda_team/adapters/metrics.py

# Confirms specialist_roles imports cleanly and both roles are registered:
.venv/bin/python -c "from mazda_team.specialist_roles import ROLES; assert sorted(ROLES) == ['sheeta', 'tarita'], sorted(ROLES); print('ok', sorted(ROLES))"

# Confirms roles.py's blurb/spoken-name/start-task completeness check passes
# (raises RuntimeError at import time if sheeta/tarita is missing from any dict):
.venv/bin/python -c "import mazda_team.roles as r; print('ok', sorted(r.ROLE_BLURBS)); print(r.delegation_acknowledgment('sheeta'))"

# Confirms routing_model wires up against specialist_roles without importing
# finance_team/receptionist_team:
.venv/bin/python -c "import mazda_team.adapters.routing_model as m; print('ok', m.__name__)"

# Confirms main.py/orchestrate.py argparse wiring works without any network call:
.venv/bin/python -m mazda_team.main --help
.venv/bin/python -m mazda_team.orchestrate --help

# Confirms mazda_team does not import finance_team or receptionist_team:
grep -rn "finance_team\|receptionist_team" mazda_team/   # expect no output

# Confirms Mazda's voice_io sends an explicit, different voice than Toyota's silence:
grep -n '"voice"' mazda_team/adapters/voice_io.py                     # expect a match
grep -n '"voice"' receptionist_team/adapters/voice_io.py              # expect NO match (Toyota's copy is untouched)
```

Manual/optional (requires `dashboard-server.service` on `localhost:8765`,
`GROQ_API_KEY`, unset `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`, `parecord`
for mic capture, `ffplay`/`mpg123`/`paplay` for playback — not required for
this plan to be considered done, since the two existing teams' own voice
paths aren't covered by the offline test suite either):

```bash
.venv/bin/python -m mazda_team.main --voice --session mazda
```

## Explicitly out of scope

- Do not modify `finance_team/` or `receptionist_team/` except that the
  builder should double check (via the `grep` verification above) that
  copying files into `mazda_team/` did not accidentally edit the originals.
- Do not touch `/home/adamsl/agent_blocks/mazda/` (the unrelated pre-existing
  system).
- Do not attempt to wire real spreadsheet or tax-form source repositories —
  none exist on this machine yet; `sheeta`/`tarita`'s outputs are clearly-
  labeled starter/placeholder deliverables per the task's "two simple agents
  to start" wording, in a self-contained `mazda_team/workspace/` directory.
- Do not add `openpyxl`/`pandas` to the venv for this pass — CSV/JSON via
  stdlib is enough for a first spreadsheet/tax-form deliverable.

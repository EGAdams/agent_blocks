# Plan: voice channel to Toyota (receptionist_team) + a real "progress" answer from finance_team

## Request

"give me a way to open up a voice channel to the receptionist_team and
discuss progress with the finance_team."

## What already exists (do not rebuild this)

`lancedb_memory/receptionist_team/main.py` already opens exactly the
requested voice channel:

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.main --voice --session toyota
```

- `--voice` wires in `receptionist_team/adapters/voice_io.py`
  (`DashboardVoiceIO`): mic capture via `parecord`, STT via
  `POST http://localhost:8765/api/voice`, TTS via
  `POST http://localhost:8765/api/tts`. This requires
  `dashboard-server.service` already running on `localhost:8765`; if it
  isn't, voice fails closed to typed input for that turn (see
  `VoiceUnavailableError` handling in `repl()`), it never guesses.
- `--session toyota` (the default) plus `session_scoped_recall=True`
  (`agent_memory/config.py`) means every voice session under that same
  session id shares one persistent LanceDB memory (`nexus/toyota/` archive),
  so things discussed in one voice call are recallable in the next one.
- Toyota (`receptionist_team/roles.py`, `TOYOTA_SYSTEM_PROMPT`) already knows
  the 7 `finance_team` roles and, via `RoutingModel`
  (`receptionist_team/adapters/routing_model.py`), can delegate a request to
  one of them and relay the result conversationally.

**The gap:** Toyota's current prompt only offers two behaviors — (1) emit
`ROUTE: <agent>` to kick off one of the 7 real, DB-touching, potentially
slow/expensive analysis agents, or (2) talk from whatever is already in the
current conversation's memory. There is no cheap way to ask "how's the
finance team doing / what have you got so far" and get an answer grounded in
what's actually on disk in `/home/adamsl/rol_finances/reports/` — the
question in the prompt ("discuss progress with the finance_team") has no
real answer today unless the user already ran everything and pasted the
results into this exact chat, or is willing to eat a full live re-run just to
ask a status question.

This plan closes that gap with one new, read-only, no-database
`finance_team` role (`progress_check`) that Toyota can route "how's it
going" style questions to, following the exact established pattern of the
three prior `finance_team` role additions (see
`specs/da6863cd_expense_reconciliation_agent.md`,
`specs/3e3e181e_expense_reconciliation_agent.md`,
`specs/d0b3f0bf_expense_reconciler_no_receipt_expected.md` for precedent and
verification style). No voice-pipeline code needs to change.

## Files to touch

### 1. `lancedb_memory/finance_team/roles.py` — add the `progress_check` role

Add after the existing `ROLES["expense_reconciler"] = AgentRole(...)` block,
at module level:

```python
ROLES[ "progress_check" ] = AgentRole(
    name="progress_check",
    output_path=f"{REPO_ROOT}/reports/finance_team_progress_report.md",
    max_turns=20,
    allowed_tools=[ "Read", "Glob" ],
    system_prompt="""\
You are a status-check agent for a team of finance analysis agents. You are
deliberately NOT given database or shell access -- you can only Read and Glob
files that other agents already wrote. Your job is to report what already
exists on disk, not to analyze anything yourself. This is READ-ONLY: never
create, edit, or delete any file except the single summary report you write
at the end.""",
    task_prompt=f"""\
Check the current state of these known finance_team deliverables (some may
not exist yet -- that is expected, just note it):
- `{REPORTS_DIR}/cash_monthly_totals.json`
- `{REPORTS_DIR}/card_monthly_totals.json`
- `{REPORTS_DIR}/bank_monthly_totals.json`
- `{REPORTS_DIR}/other_monthly_totals.json`
- `{REPORTS_DIR}/synthesis_report.html`
- `{REPO_ROOT}/reports/orphaned_receipts_report.md`
- `{REPO_ROOT}/reports/unaccounted_expenses_report.md`

1. For each path, note whether it exists.
2. For each JSON file that exists, read its `generated_at` and `row_count`
   fields (and vendor count from the `vendors` array) without touching the
   database -- just report what the file already says about itself.
3. For each HTML/markdown report that exists, skim its own summary/counts
   section (every one of these reports already starts with a summary) and
   quote its headline numbers verbatim -- do not recompute or re-derive
   anything.
4. Compare the four mappers' `generated_at` timestamps to each other: if one
   is missing or noticeably older than the other three, call that out as
   stale under its own heading.
5. Write ONE markdown file to
   `{REPO_ROOT}/reports/finance_team_progress_report.md` with a table (one
   row per deliverable above: name, present yes/no, generated_at if known,
   one-line headline pulled from step 2/3) followed by a "Stale or Missing"
   section listing anything absent or clearly out of date per step 4.
Reply with one sentence: how many of the 7 deliverables are present and
current, and name anything missing or stale.""",
)
```

Notes for the builder:
- Match this file's existing bracket-spacing style (`f( x )`,
  `dict[ str, str ]`, trailing-space-before-`]`/`)` in dataclass field lists)
  — see the surrounding `AgentRole(...)` blocks for the exact convention;
  don't reformat anything else in the file.
- `allowed_tools=["Read", "Glob"]` is the safety property that matters here:
  by construction this role cannot touch MySQL and cannot run `Bash`, so it
  can never accidentally become an expensive live analysis run — that's what
  makes it safe for Toyota to route "just checking in" questions to without
  a live-run warning.
- No `orchestrate.py` change is required: `--only` already builds its
  `choices` from `list(ROLES.keys())` dynamically, so
  `--only progress_check` works the moment the role is registered, and
  because `progress_check` is never added to `MAPPER_NAMES` or the
  default-run `names` list in `run_team()`, it is correctly excluded from a
  plain `orchestrate.py` (no-args) run — it is opt-in only, either directly
  via `--only progress_check` or via Toyota's routing.

### 2. `lancedb_memory/receptionist_team/roles.py` — teach Toyota the new role and the run-vs-check distinction

- Add one entry to `ROLE_BLURBS` (keeps the existing "must cover every
  `finance_team.ROLES` key" invariant enforced by the `_missing` check right
  below the dict — do not skip this or the module raises `RuntimeError` on
  import):

```python
    "progress_check": "read-only status check of what finance_team reports already exist and how fresh they are -- no database access, does not run a new analysis",
```

- Extend `TOYOTA_SYSTEM_PROMPT` (the f-string below `_ROSTER`) to add a
  paragraph distinguishing a status question from a run request, inserted
  between the existing "If -- and only if..." ROUTE paragraph and the
  "For everything else..." paragraph:

```python
If the user is asking about current PROGRESS or STATUS -- e.g. "how's the \
finance team doing", "what have you got so far", "did the synthesis finish", \
"is anything stale" -- that is a request for progress_check, not for a full \
analysis: reply with

ROUTE: progress_check

Never route a status/progress question to one of the six analysis agents \
(mapper_cash, mapper_card, mapper_bank, mapper_other, synthesizer, \
receipt_scanner, expense_reconciler) just because the user mentioned their \
name in passing -- only route to one of those when the user clearly wants \
that agent's analysis to run right now.
```

  (Keep the existing `{', '.join(sorted(ROLES))}` line as-is — `ROLES` now
  includes `progress_check` automatically once step 1 is done, so that
  sentence's verbatim agent-name list updates itself with no further edit.)

### 3. `lancedb_memory/README.md` — document both halves of the request

- In the `finance_team` section's role table, add a `progress_check` row:
  `| \`progress_check\` | reads existing report files only, no DB access | \`reports/finance_team_progress_report.md\` |` — note in prose immediately after the table that, unlike the other six, it is excluded from a bare `orchestrate.py` run and only runs via `--only progress_check` or Toyota's routing.
- In the `receptionist_team` section, add a short "Opening a voice channel to
  discuss finance_team progress" walkthrough:
  1. Prerequisites: `dashboard-server.service` running on `localhost:8765`;
     `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` unset; `parecord` on `PATH`
     for capture; `ffplay`/`mpg123`/`paplay` for playback.
  2. `cd lancedb_memory && .venv/bin/python -m receptionist_team.main --voice --session toyota`
  3. Example turns: ask "what's the finance team's progress so far?" (routes
     to `progress_check`, fast, no DB) vs. "run the card mapper" (routes to
     a real analysis agent, slow, touches the DB) — so the user knows which
     kind of question they're asking before they ask it.
  4. Reusing `--session toyota` across calls is what makes "discuss
     progress" possible after closing and reopening the voice channel: the
     conversation (including past route results) is recalled from the same
     persistent LanceDB session rather than starting blank each time.

## Explicitly out of scope

- No changes to `voice_io.py`, `routing_model.py`, `orchestrate.py`, or any
  dashboard code — the voice transport already satisfies "open a voice
  channel."
- No new automated test files — this repo's precedent for `finance_team`
  role additions (the three specs cited above) verifies with `py_compile`
  and a plain Python import/print, not a test suite; follow the same weight.
- No attempt to make `progress_check` reconstruct file contents beyond what
  each existing report already states about itself (its own `generated_at`,
  its own summary numbers) — it must stay database-free and stay cheap.

## Verification

Run from `lancedb_memory/`:

```bash
.venv/bin/python -m py_compile finance_team/roles.py receptionist_team/roles.py
.venv/bin/python -c "from finance_team.roles import ROLES; r = ROLES['progress_check']; print(r.output_path); print(r.allowed_tools); print(r.task_prompt)"
.venv/bin/python -c "from receptionist_team.roles import TOYOTA_SYSTEM_PROMPT, ROLE_BLURBS; assert 'progress_check' in ROLE_BLURBS; print(TOYOTA_SYSTEM_PROMPT)"
.venv/bin/python -m finance_team.orchestrate --help   # confirm progress_check appears in --only choices
git diff lancedb_memory/finance_team/roles.py lancedb_memory/receptionist_team/roles.py lancedb_memory/README.md
```

Manual confirmation of the voice channel itself (needs a live mic/speaker and
`dashboard-server.service` running, so it's a human-in-the-loop check, not
part of the automated verification above):

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.main --voice --session toyota
# say: "what's the finance team's progress so far?"
# expect Toyota to route to progress_check and read back its one-sentence summary
```

An actual end-to-end `progress_check` run additionally needs
`/home/adamsl/rol_finances` to exist with the paths above populated by a
prior `finance_team.orchestrate` run; the static checks above (import,
`py_compile`, `--help`) do not require that and are enough to confirm the
role is wired correctly before anyone spends real Claude Code turns on it.

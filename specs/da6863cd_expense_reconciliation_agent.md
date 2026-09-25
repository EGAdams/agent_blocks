# Plan: expense-reconciliation agent for finance_team

## Goal

Add a new independently-runnable agent role, `expense_reconciler`, to
`lancedb_memory/finance_team/`. It is the mirror image of `receipt_scanner`:
`receipt_scanner` finds receipt image files with no matching expense row
(orphaned files); `expense_reconciler` finds expense rows with no matching
receipt file (gaps in accounting coverage — "unaccounted expenses"). It is a
read-only reconnaissance task against the `rol_finances` repo and its MySQL
DB, producing exactly one new report file.

Read `lancedb_memory/finance_team/roles.py` and
`lancedb_memory/finance_team/orchestrate.py` in full before editing — the
snippets below are diffs against the current content, not a full replacement.

## Background already confirmed

- `finance_team/roles.py` defines `AgentRole` (a frozen dataclass — pure
  data, no SDK import) and a `ROLES: dict[str, AgentRole]` registry. Each
  role's `task_prompt` is prose instructing an LLM agent (run via
  `adapters/tool_agent.py`) to use its `Bash`/`Read`/`Write`/`Glob`/`Grep`
  tools to actually connect to MySQL (via `pymysql`, credentials from
  `/home/adamsl/planner/.env`) and walk the filesystem itself. There is no
  separate Python-side DB client or repository class in this directory —
  the agent IS the implementation, driven by its system/task prompt text.
  This is the existing, real convention for `finance_team`; do not invent a
  new architecture (no Pydantic models, no `typing.Protocol` abstractions)
  for this role unless it turns out to be unavoidable — see "Naming rule"
  below for what to do if one becomes necessary.
- `REPO_ROOT = "/home/adamsl/rol_finances"` is already defined at module
  level in `roles.py` — reuse it, do not redefine.
- `DB_CONNECTION_BRIEFING` is a shared string constant with the DB
  connection/credential instructions and the `expenses`/`categories` schema
  notes, reused (via string concatenation) by every role that touches the
  DB. Reuse it verbatim for the new role too.
- `receipt_scanner` (already in `roles.py`) is the closest sibling:
  - Its `output_path` / report is
    `f"{REPO_ROOT}/reports/orphaned_receipts_report.md"`.
  - Its system prompt documents the receipt filename convention:
    `` `{vendor_slug}_{MM}_{DD}_{YY}_{dollars}_{cents}.jpg` `` (e.g.
    `meijer_04_13_25_38_07.jpg` = Meijer, 04/13/25, $38.07), sometimes with
    an `_item_N_` infix for itemized sub-receipts.
  - Its task prompt enumerates images (jpg/jpeg/png) under four directories
    relative to `REPO_ROOT`:
    - `readable_documents/receipts/`
    - `readable_documents/generated_spreadsheets/receipts/`
    - `receipt_parsing_tools/generated_spreadsheets/`
    - `tools/receipt_scanning_tools/incoming_scans/`
  - It matches loosely: same date, amount within $0.01, description
    contains a token from the vendor slug.
  - `max_turns=60` (higher than the default 40) because it walks a lot of
    files plus does DB lookups.
- `orchestrate.py` is the composition root:
  - `MAPPER_NAMES = ["mapper_cash", "mapper_card", "mapper_bank", "mapper_other"]`.
  - `run_team()` builds `names = only or (MAPPER_NAMES + ([] if skip_receipts else ["receipt_scanner"]))`,
    runs everything in `names` except `synthesizer` concurrently via
    `asyncio.gather`, then conditionally runs `synthesizer` last if all 4
    mappers ran cleanly.
  - `argparse`'s `--only` flag already takes `choices=list(ROLES.keys())`,
    so any role added to the `ROLES` dict in `roles.py` automatically
    becomes selectable via `--only <name>` with no extra argparse wiring —
    confirm this stays true.
  - `--skip-receipts` is a dedicated boolean flag that removes
    `receipt_scanner` from the default (no `--only`) run.
- No `typing.Protocol` and no name ending in `Port` exists anywhere in
  `finance_team/` today (confirmed by grep across the whole repo — zero
  `class *Port` hits anywhere). `agent_memory/ports.py` (a different
  subsystem, `lancedb_memory/agent_memory/`) has Protocols but none of them
  are `Port`-suffixed either (`Embedder`, `MemoryStore`, `RecallStrategy`,
  `PromptComposer`, `LanguageModel`, `TurnObserver`, `Clock`). Do not touch
  that file — it is out of scope.

## Naming rule (applies only if triggered)

If, while implementing this, you find you genuinely need a
`typing.Protocol` dependency-boundary abstraction (you should not — the
established `finance_team` pattern is prompt-only, no Python-side DB/file
abstraction layer), name it `ISomeUsefulName` (I-prefix, descriptive) —
never a `*Port` suffix. This also applies if you end up touching any
existing `Port`-suffixed name anywhere in the repo, though none currently
exist in `finance_team/` or in the `agent_memory` Protocols. The expected
outcome for this task is that this rule is not triggered at all, because
the new role is pure data (an `AgentRole` entry) exactly like its siblings.

## Changes

### 1. `lancedb_memory/finance_team/roles.py`

Add two new module-level constants right after `DB_CONNECTION_BRIEFING`
(before `_mapper_role`), so the receipt-naming convention and the four
receipt directories are written once and can't drift between roles that
need them. Do **not** edit the existing `receipt_scanner` role's body — it
already has its own inline copies of this text and works; leave it alone.

```python
RECEIPT_NAMING_BRIEFING = """\
The naming convention used across this repo for receipt images is
`{vendor_slug}_{MM}_{DD}_{YY}_{dollars}_{cents}.jpg` (e.g.
"meijer_04_13_25_38_07.jpg" = Meijer, 04/13/25, $38.07), sometimes with an
"_item_N_" infix for itemized sub-receipts."""

RECEIPT_DIRS = [
    "readable_documents/receipts/",
    "readable_documents/generated_spreadsheets/receipts/",
    "receipt_parsing_tools/generated_spreadsheets/",
    "tools/receipt_scanning_tools/incoming_scans/",
]
```

Then add a new entry to `ROLES`, placed after the existing
`ROLES["receipt_scanner"] = AgentRole(...)` block:

```python
ROLES[ "expense_reconciler" ] = AgentRole(
    name="expense_reconciler",
    output_path=f"{REPO_ROOT}/reports/unaccounted_expenses_report.md",
    max_turns=60,
    system_prompt=f"""\
You are working ALONE, independent of the vendor-pattern team and of the
receipt_scanner agent, on the INVERSE of receipt_scanner's job: instead of
finding receipt image files with no matching expense row, you find expense
ROWS in the database with no matching receipt image file -- gaps in
accounting coverage. Two other automated agents also write to this repo, so
expect some churn; only report, never fix.

{RECEIPT_NAMING_BRIEFING}

This is a READ-ONLY reconnaissance task: do not INSERT/UPDATE/DELETE
anything in the database, and do not create, move, rename, or delete any
file under the repo except the single report you write in step 5 below.""",
    task_prompt=f"""\
1. Query EVERY row from `expenses` (all methods, not just one slice -- you
   are not one of the four method-sliced mapper agents).
2. Recursively find every image file (jpg/jpeg/png) under these directories
   of {REPO_ROOT}: {", ".join( f"`{d}`" for d in RECEIPT_DIRS )}.
3. Parse each filename against the vendor/date/amount convention above.
   Skip files that don't parse cleanly (that's receipt_scanner's job to
   flag as malformed, not yours) -- just build the pool of successfully
   parsed (vendor_slug, date, amount) receipts you can match against.
4. For each expense row, look for ANY parsed receipt file whose date
   matches `expense_date`, whose amount is within $0.01 of `amount`, and
   whose vendor_slug shares a token with the row's normalized `description`
   (same loose-match rule receipt_scanner uses, applied in the opposite
   direction). If no such file exists anywhere in the four directories,
   the row is UNACCOUNTED.
5. Write ONE markdown report to
   `{REPO_ROOT}/reports/unaccounted_expenses_report.md` (create the
   `reports/` directory if it doesn't exist -- do not create or touch any
   other file) with:
   - Summary: total expense rows scanned, total parsed receipt files found
     across the four directories, count and % of rows UNACCOUNTED, and a
     breakdown of the UNACCOUNTED count by `method`.
   - Unaccounted Expenses: one table sorted by `expense_date` descending,
     columns id_light, expense_date, amount, method, description
     (truncated to a reasonable length), and a short note on why no match
     was found (e.g. "no receipt file with matching date/amount/vendor in
     any of the four directories").
   - A short caveats paragraph noting this is a loose text/date/amount
     match and may have false positives if a receipt filename is malformed
     (see receipt_scanner's report for those) or a vendor token doesn't
     overlap the description wording.
""" + DB_CONNECTION_BRIEFING + """

Reply with one sentence: total expenses scanned and how many were flagged
as unaccounted.""",
)
```

Notes for the builder:
- Keep the existing bracket-spacing style (`f"""\` no leading space after
  `(`, spaces inside `[ ... ]`/`{ ... }` per the file's existing style —
  match what's already in `roles.py`, don't renormalize unrelated lines).
- `RECEIPT_DIRS` is used both to build the `Glob`-friendly directory list in
  the prompt text and is available if the builder prefers a small helper to
  format it — either is fine, the rendered prompt text is what matters, not
  how it's assembled.
- Double-check `DB_CONNECTION_BRIEFING`'s leading `\n\n` / spacing joins
  cleanly when concatenated after the task list, matching how
  `receipt_scanner`'s task_prompt already does the same concatenation.

### 2. `lancedb_memory/finance_team/orchestrate.py`

Wire `expense_reconciler` in exactly the way `receipt_scanner` is wired:
selectable standalone via `--only expense_reconciler` (this is automatic
once it's in `ROLES`, since `--only`'s `choices=list(ROLES.keys())`), plus
included in the default full-team run, plus its own dedicated skip flag
mirroring `--skip-receipts`.

Changes:

1. Update the module docstring's agent count and usage examples (it
   currently says "6-agent vendor-pattern + receipt-audit team" — becomes
   7-agent). Add an example line:
   ```
       .venv/bin/python -m finance_team.orchestrate --only expense_reconciler
   ```
   and mention `--skip-expense-check` alongside the existing
   `--skip-receipts` example.

2. In `run_team()`, change the signature and default-name assembly to add
   the new role alongside `receipt_scanner`:

   ```python
   async def run_team(
       model: str, only: list[ str ] | None, skip_receipts: bool, skip_expense_check: bool
   ) -> int:
       names = only or (
           MAPPER_NAMES
           + ( [] if skip_receipts else [ "receipt_scanner" ] )
           + ( [] if skip_expense_check else [ "expense_reconciler" ] )
       )
   ```

   Everything below that line in `run_team` (the `parallel_names`
   computation, the `asyncio.gather` fan-out, the synthesizer gating on
   `ran_all_mappers`) needs no other change: `expense_reconciler` behaves
   like `receipt_scanner` — it is not `"synthesizer"` so it lands in
   `parallel_names` automatically, and it is not one of `MAPPER_NAMES` so it
   has no effect on the `ran_all_mappers` check.

3. In `main()`, add the matching CLI flag and thread it through:

   ```python
   parser.add_argument(
       "--skip-expense-check", action="store_true",
       help="don't run the expense_reconciler agent",
   )
   ...
   exit_code = asyncio.run(
       run_team( args.model, args.only, args.skip_receipts, args.skip_expense_check )
   )
   ```

## Verification

Run from `lancedb_memory/`. These are static/structural checks — do not
attempt to actually run the agents end-to-end (that requires live DB
credentials and Claude Agent SDK subscription auth, out of scope for
verifying this change):

```bash
cd lancedb_memory
.venv/bin/python -c "
from finance_team.roles import ROLES, REPO_ROOT
assert 'expense_reconciler' in ROLES
r = ROLES['expense_reconciler']
assert r.output_path == f'{REPO_ROOT}/reports/unaccounted_expenses_report.md'
assert 'READ-ONLY' in r.system_prompt
print('roles.py OK:', r.name, r.output_path, r.max_turns)
"
.venv/bin/python -m py_compile finance_team/roles.py finance_team/orchestrate.py
.venv/bin/python -m finance_team.orchestrate --help
```

Confirm in the `--help` output that:
- `expense_reconciler` appears in the `--only` choices list.
- `--skip-expense-check` appears as a flag.

Also grep to confirm the naming rule wasn't violated and nothing new was
left half-wired:

```bash
grep -n "Port" finance_team/roles.py finance_team/orchestrate.py finance_team/adapters/tool_agent.py
grep -n "expense_reconciler" finance_team/roles.py finance_team/orchestrate.py
```

The `Port` grep should return nothing (no `Port`-suffixed names were
introduced); the `expense_reconciler` grep should show the `ROLES` entry in
`roles.py` and the wiring lines in `orchestrate.py`.

## Explicit non-goals / guardrails

- Do not write anything to the `rol_finances` MySQL DB — read-only queries
  only, exactly as the DB briefing text says.
- Do not create, move, rename, or delete any file under `REPO_ROOT` except
  `reports/unaccounted_expenses_report.md` (the agent's own report). Do not
  touch `receipt_scanner`'s report file or any mapper/synthesizer report.
- Do not modify `receipt_scanner`'s existing `AgentRole` definition, its
  wording, or its `output_path`.
- Do not modify `agent_memory/` (a different subsystem with its own
  Protocol-based ports) — out of scope for this task.
- Do not add Pydantic models or a `typing.Protocol` layer unless it turns
  out to be genuinely unavoidable for this role; the established
  `finance_team` convention is prompt-only roles with no Python-side
  DB/file abstraction. If one is added anyway, it must follow the
  `ISomeUsefulInterface` naming rule above, no `*Port` suffix.

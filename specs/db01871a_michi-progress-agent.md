# Plan: Michi — progress tracking and planning for `mazda_team`

## Request

Add a new agent, **Michi**, to `lancedb_memory/mazda_team`. She tracks
finance-report progress (by year, month, account), records completed work /
remaining tasks / blockers / unresolved transactions / verification status,
never marks work complete without evidence, maintains a persistent progress
summary and prioritized next-step plan (with responsible agent and
dependencies), flags missing/outdated reports and anything needing human
judgment, and answers "Where are we?", "What is blocking completion?", and
"What should we do next?". Mazda must be able to route to her. She
coordinates with Sheeta and TaRita only in the sense of naming them as
responsible agents in her plan — she does not do their work and they do not
gain new responsibilities in this change. Status collection and completion
math must be deterministic code; only explanation/prioritization narration
uses an LLM.

## What already exists (read this before writing code)

- `mazda_team/specialist_roles.py` — `AgentRole` dataclass + `ROLES = {"sheeta": ..., "tarita": ...}`,
  each an LLM **tool-using** agent run through the Claude Agent SDK
  (`allowed_tools=["Bash","Read","Write","Glob","Grep"]`). `REPO_ROOT` env var
  `MAZDA_TEAM_REPO_ROOT`, defaults to `mazda_team/workspace/` (created at
  runtime, currently empty — no real finance-report generation exists yet;
  Sheeta only writes one sample CSV, TaRita one sample tax-form JSON).
- `mazda_team/roles.py` — Mazda's persona: `ROLE_BLURBS` / `ROLE_SPOKEN_NAMES` /
  `ROLE_START_TASKS` dicts (one key per name in `specialist_roles.ROLES`, a
  loop right below raises `RuntimeError` at import if any name is missing
  from one of the three dicts), `delegation_acknowledgment()`,
  `MAZDA_SYSTEM_PROMPT` (an f-string ending in a `ROUTE: <agent>` protocol,
  `Allowed agent names: {', '.join(sorted(ROLES))}`).
- `mazda_team/adapters/routing_model.py` — `RoutingModel(inner, model)`
  wraps a `LanguageModel`. On a bare `ROUTE: <name> [| extra]` reply from the
  inner model, it looks up `specialist_roles.ROLES[name]` and runs it via
  `run_role()` (a full Claude Agent SDK subprocess with real tool use).
  Unknown names get an error reply listing `sorted(ROLES)`.
- `mazda_team/adapters/tool_agent.py` — `run_role(role, cwd, model)`, spawns
  `claude_agent_sdk.query()`. This is infrastructure for **tool-using**
  agents; Michi does not need it (she has no tools to run, no files to
  write).
- `mazda_team/main.py` — composition root. Builds `GroqLanguageModel` for
  Mazda's own chat, wraps it in `RoutingModel(RetryingModel(inner), model=role_model)`.
  `GroqLanguageModel(model=..., system_prompt=...)` (`agent_memory/adapters/groq_model.py`)
  is a plain OpenAI-SDK-over-HTTP call to Groq; constructing it reads
  `GROQ_API_KEY` from the environment but only fails when `.complete()` is
  actually called without a key set — **actually verified: `OpenAI(api_key=None, ...)`
  raises `OpenAIError` immediately at construction time**, so any code path
  that eagerly builds a `GroqLanguageModel` requires `GROQ_API_KEY` to already
  be set. Keep this in mind: the new code must only construct a default Groq
  explainer lazily, inside a request that actually needs it, never in
  `RoutingModel.__init__`, or every test/import that doesn't set
  `GROQ_API_KEY` breaks.
- `finance_team/roles.py`'s `progress_check` role (and its own spec,
  `specs/db33e572_finance-progress-voice-channel.md`) is the closest
  precedent for a "status" agent, but it works differently than what's asked
  here: it is itself a Claude-tool-using agent (Read/Glob only) that asks an
  LLM to read report files and summarize them — the LLM does the fact-finding.
  This task explicitly asks for the opposite split: **deterministic code**
  collects status and computes completion; the LLM is only used to *explain*
  already-computed facts and *propose* an order for already-identified next
  steps. Do not reuse `progress_check`'s shape for Michi.
- `agent_memory/ports.py` — `LanguageModel` Protocol: `async def complete(self, prompt: str) -> AgentReply`.
  `agent_memory/models/agent_reply.py` — `AgentReply(text, input_tokens=None, output_tokens=None, cost_usd=None)`,
  a Pydantic `BaseModel`. `tests/fakes.py` — `ScriptedModel` (records prompts,
  replays queued replies) is the existing fake `LanguageModel`; reuse it,
  don't write a new one.
- No `pytest` in the venv. Tests in this repo are hand-rolled: each test file
  defines `async def test_*()` functions, then a small `main()` collects and
  runs them with `asyncio.run`, printing PASS/FAIL per test (see
  `tests/test_agent.py`). Follow this exact style for new tests; do not add a
  pytest dependency.
- `claude_agent_sdk` imports cleanly with no network/auth side effect (only
  needed by `tool_agent.py`, which Michi's own tests never need to import
  with real behavior — mock `run_role` if a test happens to touch the
  sheeta/tarita path).

## Design

### 1. On-disk contract for finance reports (new, deterministic-only)

Sheeta is the eventual writer of these; **this task does not change what
Sheeta generates** (report generation stays with her, per the request) — it
only defines the schema/location Michi reads, and Michi must honestly report
"not started" / "missing" for everything until real reports land there. Note
this explicitly as a limitation in the final summary.

Convention: `{REPO_ROOT}/finance_reports/<year>/<month:02d>/<account>.json`,
one JSON object per (year, month, account):

```json
{
  "year": 2025, "month": 6, "account": "checking",
  "transactions": [
    {"id": "t1", "description": "Rent", "amount": 1200.0, "resolved": true},
    {"id": "t2", "description": "Unknown ACH", "amount": 43.10, "resolved": false}
  ],
  "generated_at": "2025-06-20T10:00:00+00:00",
  "generated_by": "sheeta",
  "notes": null
}
```

### 2. New package: `mazda_team/progress/`

Ordinary PEP8 spacing (matches `roles.py`/`main.py`/`routing_model.py`, the
files this package plugs into — not the padded-bracket style of
`specialist_roles.py`/`tool_agent.py`, which this package does not touch).

**`mazda_team/progress/__init__.py`** — one-line module docstring: "Michi's
deterministic progress tracking (`tracker.py`, `schema.py`) and LLM
explanation layer (`narrate.py`, `michi.py`)."

**`mazda_team/progress/schema.py`** — Pydantic boundary models only, no
filesystem/LLM imports:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Severity = Literal["blocker", "missing", "human_judgment", "ready"]
Verification = Literal["verified", "unknown"]
Status = Literal["not_started", "in_progress", "blocked", "complete"]


class TransactionRecord(BaseModel):
    id: str
    description: str = ""
    amount: float | None = None
    resolved: bool = False


class FinanceReport(BaseModel):
    """On-disk contract Sheeta writes to
    finance_reports/<year>/<month>/<account>.json."""
    year: int
    month: int
    account: str
    transactions: list[TransactionRecord] = []
    generated_at: datetime | None = None
    generated_by: str | None = None
    notes: str | None = None


class AccountPeriodStatus(BaseModel):
    year: int
    month: int
    account: str
    status: Status
    verification: Verification
    completed_work: list[str] = []
    remaining_tasks: list[str] = []
    blockers: list[str] = []
    unresolved_transactions: list[str] = []
    evidence: list[str] = []   # paths / facts actually observed
    notes: str | None = None   # e.g. a parse-error message


class NextStepCandidate(BaseModel):
    description: str
    responsible_agent: str
    dependencies: list[str] = []
    severity: Severity
    year: int | None = None
    month: int | None = None
    account: str | None = None


class ProgressSnapshot(BaseModel):
    generated_at: datetime
    entries: list[AccountPeriodStatus]
    missing_or_outdated: list[str] = []
    needs_human_judgment: list[str] = []
    next_steps: list[NextStepCandidate] = []
```

**`mazda_team/progress/tracker.py`** — deterministic only, no LLM/network
imports. Module docstring: "Deterministic collection of Michi's
finance-report progress. No LLM calls here — narrate.py is the only place an
LLM is involved." Public (no leading underscore — the tests call these
directly) functions/constants:

```python
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from .schema import AccountPeriodStatus, FinanceReport, NextStepCandidate, ProgressSnapshot
from ..specialist_roles import REPO_ROOT

FINANCE_REPORTS_DIRNAME = "finance_reports"
STATE_RELATIVE_PATH = "progress/michi_state.json"
DEFAULT_EXPECTED_ACCOUNTS = ("checking", "savings", "credit_card")

EXPECTED_ACCOUNTS = tuple(
    a.strip() for a in os.environ.get("MAZDA_TEAM_EXPECTED_ACCOUNTS", "").split(",") if a.strip()
) or DEFAULT_EXPECTED_ACCOUNTS
STALE_DAYS = int(os.environ.get("MAZDA_TEAM_STALE_DAYS", "30"))
MONTHS_BACK = int(os.environ.get("MAZDA_TEAM_MONTHS_BACK", "3"))


def finance_reports_dir(repo_root: str) -> Path: ...
def state_path(repo_root: str) -> Path: ...

def expected_periods(today: date, months_back: int = MONTHS_BACK) -> list[tuple[int, int]]:
    """(year, month) for the current month and the months_back-1 before it, oldest first."""

def report_path(repo_root: str, year: int, month: int, account: str) -> Path: ...

def load_report(path: Path) -> tuple[FinanceReport | None, str | None]:
    """Never raises. Returns (None, None) if the file doesn't exist,
    (None, "<error>") if it exists but fails to parse/validate,
    (report, None) on success."""

def status_for_period(repo_root: str, year: int, month: int, account: str) -> AccountPeriodStatus:
    """
    - file missing -> status="not_started", verification="verified"
      (absence is itself a verified filesystem fact), remaining_tasks=["produce the ... report"].
    - file fails to parse/validate -> status="blocked", verification="unknown",
      blockers=[the parse error], notes=the parse error. NEVER "complete".
    - file parses but its own year/month/account fields disagree with the
      path they were found at -> status="blocked", verification="unknown",
      blockers=[a message naming both the path-derived and file-stated values].
    - file parses, fields agree, but has >=1 transaction with resolved=False
      -> status="in_progress", verification="verified",
      remaining_tasks=[f"resolve N unresolved transaction(s)"],
      blockers=[one entry per unresolved transaction],
      unresolved_transactions=[their descriptions/ids]. NEVER "complete".
    - file parses, fields agree, all transactions resolved (including the
      zero-transaction case) -> status="complete", verification="verified",
      completed_work=[a summary sentence].
    Every branch sets evidence=[str(path)] (or a "does not exist" note for
    the missing case) so a caller can see exactly what was inspected.
    """

def iter_all_report_files(repo_root: str):
    """Yield (year, month, account, path) for every *.json under
    finance_reports_dir(repo_root)/<digits>/<digits>/*.json, skipping any
    non-numeric year/month directory names rather than raising."""

def scan_unexpected(repo_root: str, periods: list[tuple[int, int]], expected_accounts) -> list[AccountPeriodStatus]:
    """Every file found by iter_all_report_files whose (year, month, account)
    is NOT already covered by periods x expected_accounts, run through
    status_for_period too (so no on-disk work is silently ignored)."""

def missing_or_outdated(entries: list[AccountPeriodStatus], today: date) -> list[str]:
    """One string per not_started entry ("... report missing"), plus one
    string per in_progress entry whose evidence file's mtime is more than
    STALE_DAYS old ("... N day(s) old and still has unresolved transactions")."""

def needs_human_judgment(entries: list[AccountPeriodStatus], expected_accounts) -> list[str]:
    """One string per blocked entry (using its notes), plus one string per
    entry whose account is not in expected_accounts (flag as unconfirmed)."""

def next_step_candidates(entries: list[AccountPeriodStatus], expected_accounts) -> list[NextStepCandidate]:
    """
    - not_started entry -> NextStepCandidate(description="Produce the {account}
      finance report for {year}-{month:02d}", responsible_agent="sheeta",
      severity="missing", year=, month=, account=).
    - in_progress entry -> description="Resolve {n} unresolved transaction(s)
      in the {account} report for {year}-{month:02d}", responsible_agent="sheeta",
      severity="blocker".
    - blocked entry -> description="Review the {account} report for
      {year}-{month:02d}: {notes}", responsible_agent="human",
      severity="human_judgment".
    - for each year present among entries whose account is in
      expected_accounts: if every such entry for that year has
      status=="complete", add one NextStepCandidate(description=f"All {year}
      account reports are verified complete -- ready for tarita to prepare
      the {year} tax form", responsible_agent="tarita", severity="ready",
      dependencies=[f"sheeta: all {year} reports complete"], year=year).
    Sort the final list by (severity_rank, year, month, account) where
    severity_rank = {"blocker": 0, "missing": 1, "human_judgment": 2, "ready": 3}
    (None year/month sort first within a severity via `or 0`).
    """

def collect_progress(repo_root: str, today: date | None = None, expected_accounts=None) -> ProgressSnapshot:
    """today defaults to date.today(); expected_accounts defaults to module
    EXPECTED_ACCOUNTS (both overridable so tests are deterministic and don't
    need to mutate env vars). Builds entries for periods x expected_accounts
    via status_for_period, extends with scan_unexpected, then fills
    missing_or_outdated / needs_human_judgment / next_steps from the helpers
    above. generated_at=datetime.now(timezone.utc)."""

def save_snapshot(snapshot: ProgressSnapshot, path: Path) -> None:
    """path.parent.mkdir(parents=True, exist_ok=True); path.write_text(snapshot.model_dump_json(indent=2))."""

def load_snapshot(path: Path) -> ProgressSnapshot | None:
    """None if path doesn't exist, else ProgressSnapshot.model_validate_json(path.read_text())."""

def collect_and_persist(repo_root: str, today: date | None = None, expected_accounts=None) -> ProgressSnapshot:
    """collect_progress(...) then save_snapshot(..., state_path(repo_root)); returns the snapshot.
    This IS the "persistent progress summary and prioritized next-step plan"
    the request asks for -- every Michi invocation refreshes and overwrites
    {REPO_ROOT}/progress/michi_state.json with the current deterministic facts."""
```

**`mazda_team/progress/narrate.py`** — the only file allowed to shape an LLM
prompt/system-prompt for Michi. No filesystem access.

```python
from .schema import ProgressSnapshot

DEFAULT_PROGRESS_QUESTION = (
    "Give a status update: what is done, what is blocking completion, "
    "and what should happen next for the finance reports."
)

MICHI_SYSTEM_PROMPT = """\
You are Michi, the progress and planning tracker for a small finance-report
team (Sheeta writes the reports, TaRita prepares tax forms from them). You do
not write reports or tax forms yourself.

You are given a machine-generated snapshot of verified facts: one entry per
year, month, and account, each with a status, a verification flag, and any
blockers, unresolved transactions, or evidence. You are also given next-step
candidates the code already identified, each with a responsible agent and
any dependencies, plus items flagged as missing/outdated or needing human
judgment.

Use ONLY the facts in the snapshot. Never say work is complete unless its
status says "complete" and verification says "verified". If verification
says "unknown", say plainly that it could not be confirmed instead of
guessing. If you add any interpretation beyond the snapshot's own facts,
label it clearly as your own inference, not a verified fact.

Explain the findings conversationally and propose a priority order for the
next steps, using the responsible_agent and dependencies already given.
Flag anything needing human judgment as something a person must decide, not
something you or an agent can resolve.

Your reply is relayed to the user by Mazda. Use plain text, contractions,
short natural sentences -- no Markdown, headings, or bullet lists.
"""


def build_explainer_prompt(snapshot: ProgressSnapshot, question: str) -> str:
    """Render QUESTION, then one line per entry ("- checking 2025-06:
    status=in_progress verification=verified blockers=[...] remaining=[...]"),
    then a MISSING OR OUTDATED section, a NEEDS HUMAN JUDGMENT section, and a
    NEXT-STEP CANDIDATES section (one line per candidate: severity,
    description, responsible agent, dependencies) -- all straight string
    formatting, no LLM call in this function."""
```

**`mazda_team/progress/michi.py`** — the composition point `RoutingModel`
calls:

```python
from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from .. import specialist_roles
from . import tracker
from .narrate import DEFAULT_PROGRESS_QUESTION, build_explainer_prompt

AGENT_NAME = "michi"


async def answer(question: str, explainer: LanguageModel, repo_root: str | None = None) -> AgentReply:
    root = repo_root or specialist_roles.REPO_ROOT
    snapshot = tracker.collect_and_persist(root)
    prompt = build_explainer_prompt(snapshot, question.strip() or DEFAULT_PROGRESS_QUESTION)
    return await explainer.complete(prompt)
```

**`mazda_team/progress/cli.py`** — manual/no-LLM verification helper:

```python
"""Print Michi's deterministic progress snapshot with no LLM call.

    .venv/bin/python -m mazda_team.progress.cli
"""
import argparse

from .. import specialist_roles
from . import tracker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=specialist_roles.REPO_ROOT)
    args = parser.parse_args()
    snapshot = tracker.collect_and_persist(args.repo_root)
    print(snapshot.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
```

### 3. `mazda_team/roles.py` — register Michi with Mazda

- Add `from .progress.michi import AGENT_NAME as MICHI_AGENT_NAME`.
- Add `ALL_AGENT_NAMES = sorted(set(ROLES) | {MICHI_AGENT_NAME})`.
- Add one entry to each of `ROLE_BLURBS`, `ROLE_SPOKEN_NAMES`,
  `ROLE_START_TASKS` for `"michi"`, e.g.:
  - blurb: `"tracks finance-report progress and plans next steps; does not write reports or tax forms herself"`
  - spoken name: `"our progress tracker"`
  - start task: `"checking finance-report progress and what's next"`
- Change the completeness-check loop's `_missing = set(ROLES) - set(_mapping)`
  to `_missing = set(ALL_AGENT_NAMES) - set(_mapping)` (keeps the "every
  agent name has a blurb/spoken-name/start-task" invariant, now including
  Michi).
- In `delegation_acknowledgment()`, change `if agent_name not in ROLES:` to
  `if agent_name not in ALL_AGENT_NAMES:`.
- Change `_ROSTER = "\n".join(... for name in ROLES)` to
  `for name in ALL_AGENT_NAMES`.
- In `MAZDA_SYSTEM_PROMPT`, change
  `Allowed agent names: {', '.join(sorted(ROLES))}.` to
  `Allowed agent names: {', '.join(ALL_AGENT_NAMES)}.`, and insert a new
  paragraph (mirroring Toyota's precedent in
  `specs/db33e572_finance-progress-voice-channel.md`) between the existing
  ROUTING PROTOCOL section and ORDINARY CONVERSATION:

  ```
  PROGRESS AND PLANNING

  If the user is asking about current PROGRESS or PLANNING -- e.g. "where \
  are we", "what's blocking completion", "what should we do next", "is \
  anything missing or overdue" -- route to michi, not to sheeta or tarita:

  ROUTE: michi
  ROUTE: michi | focus on the checking account

  michi only reads and reasons about existing reports and data; she never \
  writes a spreadsheet or tax form herself. A progress question about \
  sheeta's or tarita's work still goes to her, not to them, even if the \
  user names one of them.
  ```

### 4. `mazda_team/adapters/routing_model.py` — wire the route

```python
import re
from dataclasses import replace
from pathlib import Path

from agent_memory.adapters.groq_model import GroqLanguageModel
from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from ..progress.michi import AGENT_NAME as MICHI_AGENT_NAME, answer as michi_answer
from ..progress.narrate import DEFAULT_PROGRESS_QUESTION, MICHI_SYSTEM_PROMPT
from ..specialist_roles import REPO_ROOT, ROLES
from .tool_agent import run_role

_ROUTE_RE = re.compile(r"ROUTE:\s*([A-Za-z_]+)\s*(?:\|\s*(.*))?", re.DOTALL)
_ALL_ROUTE_NAMES = sorted(set(ROLES) | {MICHI_AGENT_NAME})


class RoutingModel:
    def __init__(
        self,
        inner: LanguageModel,
        model: str,
        explainer: LanguageModel | None = None,
        repo_root: str | None = None,
    ) -> None:
        self._inner = inner
        self._role_model = model
        self._explainer = explainer
        self._repo_root = repo_root

    async def complete(self, prompt: str) -> AgentReply:
        reply = await self._inner.complete(prompt)
        match = _ROUTE_RE.fullmatch(reply.text.strip())
        if not match:
            return reply

        role_name, extra = match.group(1), (match.group(2) or "").strip()

        if role_name == MICHI_AGENT_NAME:
            print(f"\n[Mazda -> {role_name}] checking finance-report progress...")
            explainer = self._explainer or GroqLanguageModel(system_prompt=MICHI_SYSTEM_PROMPT)
            question = extra or DEFAULT_PROGRESS_QUESTION
            return await michi_answer(question, explainer=explainer, repo_root=self._repo_root)

        role = ROLES.get(role_name)
        if role is None:
            return AgentReply(
                text=(
                    f'I tried to route that to "{role_name}", but I don\'t have '
                    f"an agent by that name. I know: {', '.join(_ALL_ROUTE_NAMES)}."
                )
            )
        # ... unchanged from here (extra -> replace(role, task_prompt=...), run_role(...), etc.)
```

Critical detail: the default `GroqLanguageModel(system_prompt=MICHI_SYSTEM_PROMPT)`
is built **lazily inside `complete()`, only on an actual `michi` route**, not
in `__init__`. Building it eagerly in `__init__` would make constructing a
`RoutingModel` require `GROQ_API_KEY` even for tests that never exercise the
michi path (verified above: `OpenAI(api_key=None, ...)` raises immediately).

`main.py` needs **no change** — it already constructs
`RoutingModel(RetryingModel(inner_model), model=role_model)`; the two new
constructor parameters are optional and default to the lazy/real behavior.

### 5. `lancedb_memory/README.md` — document Michi

In the existing `## mazda_team` section: add a `michi` row to the agent
table (`| \`michi\` | Tracks finance-report progress by year/month/account and plans next steps; reads existing reports and \`workspace/progress/michi_state.json\`, writes nothing but that state file |`), and one short paragraph after the table:

> Michi's status and completion checks are deterministic Python
> (`mazda_team/progress/tracker.py`) — an LLM is only used to explain her
> findings and propose a next-step order (`mazda_team/progress/narrate.py`).
> She reads finance reports from `workspace/finance_reports/<year>/<month>/<account>.json`,
> a schema no agent writes yet in this repo (Sheeta is the intended future
> writer) — until then she honestly reports everything as not started.
> Ask Mazda things like "where are we with the finance reports" or "what's
> blocking completion" to route to her.

## Tests to add

New self-contained test package, `mazda_team/tests/`, following the repo's
hand-rolled async-test-function convention (see `tests/test_agent.py`) —
**no pytest**. Add a tiny shared runner so the pattern isn't copy-pasted
three times:

**`mazda_team/tests/__init__.py`** — empty.

**`mazda_team/tests/runner.py`**:
```python
"""Shared hand-rolled async test harness (mirrors tests/test_agent.py's
inline runner) so mazda_team's test files don't each reimplement it."""
import asyncio
import sys


async def _run(namespace: dict) -> int:
    tests = [v for k, v in sorted(namespace.items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            await test()
            print(f"PASS  {test.__name__}")
        except AssertionError as oops:
            failures += 1
            print(f"FAIL  {test.__name__}: {oops}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


def main(namespace: dict) -> None:
    sys.exit(asyncio.run(_run(namespace)))
```

Each test file starts with:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # -> lancedb_memory/
```
and ends with:
```python
if __name__ == "__main__":
    from mazda_team.tests.runner import main
    main(globals())
```

**`mazda_team/tests/test_progress_tracker.py`** — uses `tempfile.TemporaryDirectory()`
per test as `repo_root`, writes fixture files under
`<repo_root>/finance_reports/<year>/<month>/<account>.json`, always passes an
explicit `today=date(2025, 6, 15)` (never real "today") for determinism:

- `test_missing_report_is_not_started_with_evidence` — empty repo_root ->
  every entry from `collect_progress` has `status="not_started"`,
  `verification="verified"`, and non-empty `evidence`; `missing_or_outdated`
  lists all of them; `next_steps` contains one `severity="missing"`,
  `responsible_agent="sheeta"` candidate per entry.
- `test_all_transactions_resolved_is_complete` — one report, two
  `resolved=true` transactions -> that entry's `status="complete"`,
  `verification="verified"`, `blockers=[]`.
- `test_unresolved_transaction_is_in_progress_never_complete` — one
  resolved + one `resolved=false` transaction -> `status="in_progress"`
  (never `"complete"`), `blockers` names the unresolved transaction,
  `unresolved_transactions` has length 1, a matching `severity="blocker"`
  next step for `sheeta` exists.
- `test_malformed_json_is_blocked_and_unknown` — write `not valid json` to
  the report path -> `status="blocked"`, `verification="unknown"` (this is
  the core "never mark complete without evidence, distinguish unknown from
  verified" check), and a matching string appears in
  `needs_human_judgment`.
- `test_path_field_mismatch_is_blocked` — write a syntactically valid report
  whose internal `"month"` disagrees with the directory it's placed in ->
  `status="blocked"`, `verification="unknown"`, blocker text names both
  values.
- `test_unexpected_account_flagged_for_human_review` — pass
  `expected_accounts=("checking",)` explicitly to `collect_progress`, write a
  report for `"joint"` -> it still appears in `entries` (via
  `scan_unexpected`) and a "not in the configured expected list" string
  appears in `needs_human_judgment`.
- `test_year_complete_triggers_tarita_ready_step` — build (directly via
  `next_step_candidates`, not full `collect_progress`, to avoid depending on
  `expected_periods`' rolling window) a fixed list of `AccountPeriodStatus`
  covering every month for one account/year, all `status="complete"` ->
  assert one `severity="ready"`, `responsible_agent="tarita"` candidate for
  that year exists; a second case with one incomplete month -> assert no such
  candidate exists.
- `test_next_step_priority_ordering` — hand-build entries producing one
  candidate of each severity out of order -> assert
  `next_step_candidates(...)` returns them sorted
  `blocker, missing, human_judgment, ready`.
- `test_persistence_round_trip` — `collect_and_persist(repo_root, today=...)`
  -> assert `tracker.state_path(repo_root)` exists on disk -> `load_snapshot(...)`
  returns a `ProgressSnapshot` whose `.model_dump(mode="json")` equals the
  original snapshot's (this is the "persistent progress summary" guarantee).

**`mazda_team/tests/test_michi_routing.py`** — imports
`from tests.fakes import ScriptedModel` (reuse the existing fake, don't
duplicate it), and `from mazda_team.adapters.routing_model import RoutingModel`.
Always construct `RoutingModel(inner=..., model="fake-role-model", explainer=..., repo_root=<tmp dir>)`
so no test needs `GROQ_API_KEY` or a live Claude subprocess:

- `test_progress_question_routes_to_michi_not_tool_agent` — `inner =
  ScriptedModel(["ROUTE: michi | focus on checking"])`,
  `explainer = ScriptedModel(["Here is the status."])`, empty tmp
  `repo_root`. `await routing.complete("where are we?")` ->
  `reply.text == "Here is the status."`; `explainer.prompts[0]` contains
  `"focus on checking"`, `"QUESTION:"`, and at least one deterministic fact
  string such as `"not_started"` (proves the real snapshot was embedded, not
  a stub). Also assert `tracker.state_path(repo_root)` now exists (proves
  the deterministic collector actually ran and persisted).
- `test_ordinary_reply_passes_through_and_never_calls_explainer` — `inner =
  ScriptedModel(["just chatting, no route"])`. Reply text equals that string;
  `explainer.prompts == []`.
- `test_unknown_route_name_lists_michi_too` — `inner = ScriptedModel(["ROUTE: bogus"])` ->
  reply text contains `"michi"` (fixes the pre-existing error-message list to
  include Michi alongside `sheeta`/`tarita`).
- `test_sheeta_route_still_uses_tool_agent_unaffected` — `inner =
  ScriptedModel(["ROUTE: sheeta"])`; monkeypatch
  `mazda_team.adapters.routing_model.run_role` (via `unittest.mock.patch`)
  with a fake async function returning a stub `RoleResult`, so no real Claude
  Agent SDK subprocess is spawned; assert the fake was called with the
  `sheeta` `AgentRole` and that `explainer.prompts == []` (proves Michi's
  path was not touched for a sheeta request).

**`mazda_team/tests/test_roles_registration.py`**:

- `test_all_agent_names_includes_michi` —
  `from mazda_team.roles import ALL_AGENT_NAMES; assert "michi" in ALL_AGENT_NAMES`.
- `test_import_succeeds_meaning_completeness_dicts_cover_michi` — a bare
  `import mazda_team.roles` succeeding IS the proof (the module-level loop
  raises `RuntimeError` at import time otherwise); additionally assert
  `"michi" in mazda_team.roles.ROLE_BLURBS` etc. explicitly for a readable
  failure message if the invariant ever regresses.
- `test_delegation_acknowledgment_for_michi` —
  `delegation_acknowledgment("michi")` returns a string mentioning her
  spoken name and doesn't raise.
- `test_system_prompt_routes_progress_questions_to_michi` — assert
  `"ROUTE: michi"` appears in `MAZDA_SYSTEM_PROMPT` and `"michi"` appears in
  the `Allowed agent names:` line.

## Verification

Run from `lancedb_memory/`:

```bash
.venv/bin/python -m py_compile mazda_team/progress/__init__.py mazda_team/progress/schema.py \
  mazda_team/progress/tracker.py mazda_team/progress/narrate.py mazda_team/progress/michi.py \
  mazda_team/progress/cli.py mazda_team/roles.py mazda_team/adapters/routing_model.py \
  mazda_team/tests/runner.py mazda_team/tests/test_progress_tracker.py \
  mazda_team/tests/test_michi_routing.py mazda_team/tests/test_roles_registration.py

# Confirms Michi is a recognized route without needing GROQ_API_KEY or a live DB:
.venv/bin/python -c "import mazda_team.roles as r; assert 'michi' in r.ALL_AGENT_NAMES; print('ok')"

# No-LLM manual look at a fresh (empty) workspace -- expect every entry
# not_started, honestly, since no report-writer exists yet:
.venv/bin/python -m mazda_team.progress.cli

# The new test files (no pytest, no network, no GROQ_API_KEY needed):
.venv/bin/python mazda_team/tests/test_progress_tracker.py
.venv/bin/python mazda_team/tests/test_michi_routing.py
.venv/bin/python mazda_team/tests/test_roles_registration.py

# Confirm nothing else regressed:
.venv/bin/python tests/test_agent.py
.venv/bin/python tests/test_lancedb_store.py
.venv/bin/python tests/test_embedding_profiles.py

grep -n "GroqLanguageModel" mazda_team/adapters/routing_model.py   # only inside complete(), not __init__
git diff mazda_team/roles.py mazda_team/adapters/routing_model.py lancedb_memory/README.md 2>/dev/null || \
  git diff mazda_team/roles.py mazda_team/adapters/routing_model.py README.md
```

Manual/optional (needs `GROQ_API_KEY`, unset `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`):

```bash
.venv/bin/python -m mazda_team.main
# type: "where are we with the finance reports?"
# expect a ROUTE: michi turn and a spoken-style answer describing
# everything as not started (workspace/finance_reports/ is empty on a fresh checkout)
```

## Explicitly out of scope / known limitations to state in the final summary

- Sheeta's and TaRita's own `system_prompt`/`task_prompt` in
  `specialist_roles.py` are **not modified** — they still only produce their
  one sample CSV / sample tax-form JSON. Nothing yet writes into
  `finance_reports/<year>/<month>/<account>.json`, so a fresh run of Michi
  will honestly report everything as `not_started`/missing. Wiring Sheeta up
  to that schema is a natural follow-up, not part of this task (report
  generation stays with Sheeta/TaRita per the request).
- `mazda_team/orchestrate.py` is **not changed** — Michi has no
  `AgentRole`/Claude-tool shape to run through it; she is only reachable via
  Mazda's `ROUTE: michi`, or directly via `python -m mazda_team.progress.cli`
  for a no-LLM deterministic check.
- `EXPECTED_ACCOUNTS` (`checking`, `savings`, `credit_card`) and the
  3-month rolling window are placeholders, configurable via
  `MAZDA_TEAM_EXPECTED_ACCOUNTS` / `MAZDA_TEAM_MONTHS_BACK` /
  `MAZDA_TEAM_STALE_DAYS` env vars — there is no real account list on this
  machine yet, same "starter/placeholder" status as the rest of `mazda_team`.
- The LLM explanation layer is instructed (not code-enforced) to label its
  own inferences as such; only the deterministic layer's "verified" vs
  "unknown" distinction is guaranteed by code.

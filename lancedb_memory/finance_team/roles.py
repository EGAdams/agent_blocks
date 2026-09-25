"""Role definitions -- pure data, no SDK import (mirrors agent_memory/ports.py
being the only Protocol layer: nothing here names claude_agent_sdk)."""
from dataclasses import dataclass, field

REPO_ROOT = "/home/adamsl/rol_finances"
REPORTS_DIR = f"{REPO_ROOT}/reports/vendor_patterns"

DB_CONNECTION_BRIEFING = """\
Connect with pymysql. Credentials come from environment variables that live in
/home/adamsl/planner/.env: NON_PROFIT_USER, NON_PROFIT_PASSWORD,
NON_PROFIT_DB_NAME (defaults to nonprofit_finance), DB_HOST (defaults to
127.0.0.1). Load that .env yourself (e.g. `set -a; source
/home/adamsl/planner/.env; set +a` before running your python, or read it and
export the vars) -- it is not exported in a fresh shell.

expenses table columns you need: id, id_light, expense_date (date), amount
(decimal, ALWAYS a positive magnitude, never signed), category_id, description,
method (enum CASH/CARD/BANK/OTHER), merchant_id, source_file. Categories table:
id, parent_id, name -- walk parent_id to bucket a vendor if useful, but vendor
identity for this task should come from `description` (normalize case/
whitespace/trailing store numbers yourself; there is no clean vendor_id
column) rather than category.

This is a READ-ONLY task. Do not INSERT/UPDATE/DELETE anything in the
database. Only write your report file(s)."""


@dataclass( frozen=True )
class AgentRole:
    name: str
    output_path: str
    system_prompt: str
    task_prompt: str
    allowed_tools: list[ str ] = field(
        default_factory=lambda: [ "Bash", "Read", "Write", "Glob", "Grep" ]
    )
    max_turns: int = 40


def _mapper_role( method: str, description: str ) -> AgentRole:
    out = f"{REPORTS_DIR}/{method.lower()}_monthly_totals.json"
    return AgentRole(
        name=f"mapper_{method.lower()}",
        output_path=out,
        system_prompt=(
            "You are a finance data-mining agent working alone on ONE disjoint "
            f"slice of the expenses table: rows where method = '{method}' "
            f"({description}). Three other agents own the other three method "
            "values in parallel -- do not touch rows outside your slice, and "
            "do not read or write their output files.\n\n" + DB_CONNECTION_BRIEFING
        ),
        task_prompt=f"""\
1. Query every row from `expenses` where method = '{method}'.
2. Normalize each row's `description` into a vendor key (trim, uppercase,
   collapse whitespace, strip trailing store/reference numbers like "#1234"
   or a trailing transaction id) so the same real-world vendor collapses to
   one key even with minor text drift across statements.
3. Build a vendor x month total-spend matrix (YYYY-MM buckets) covering all
   months present in the data.
4. For every vendor with at least 3 distinct months of activity, compute:
   - months_present: sorted list of YYYY-MM it appears in
   - typical_amount: median of its monthly totals
   - amount_stddev
   - cadence: your best label for the interval pattern -- "monthly",
     "biweekly", "quarterly", "irregular", etc., based on the actual gaps
     between months_present
   - last_amount and last_month
5. Write ONE JSON file to `{out}` (create the `reports/vendor_patterns/`
   directory if it doesn't exist) shaped like:
   {{
     "method": "{method}",
     "generated_at": "<ISO8601>",
     "row_count": <int>,
     "vendors": [
       {{"vendor_key": "...", "months_present": [...], "typical_amount": 0.0,
         "amount_stddev": 0.0, "cadence": "...", "last_amount": 0.0,
         "last_month": "..."}}
       ...
     ]
   }}
Do not produce prose output -- your deliverable is the JSON file. When done,
reply with one sentence: how many vendors and rows you processed.""",
    )


ROLES: dict[ str, AgentRole ] = {
    r.name: r
    for r in [
        _mapper_role( "CASH", "cash purchases" ),
        _mapper_role( "CARD", "card swipes -- retail/vendor spend" ),
        _mapper_role( "BANK", "ACH / online transfers -- likely home of recurring bills" ),
        _mapper_role( "OTHER", "paper checks and everything uncategorized by method" ),
    ]
}

ROLES[ "synthesizer" ] = AgentRole(
    name="synthesizer",
    output_path=f"{REPORTS_DIR}/synthesis_report.html",
    system_prompt=(
        "You are the reduce step of a 5-agent vendor-payment-pattern team. "
        "Four other agents already wrote per-method JSON reports of vendor "
        "monthly totals and recurrence cadence, one file per `method` enum "
        "value (CASH/CARD/BANK/OTHER). Your job is to merge them and flag "
        "what a bookkeeper needs to see. " + DB_CONNECTION_BRIEFING
    ),
    task_prompt=f"""\
1. Read all four JSON files in `{REPORTS_DIR}/` (cash_monthly_totals.json,
   card_monthly_totals.json, bank_monthly_totals.json,
   other_monthly_totals.json). If one is missing, note that and continue with
   what exists.
2. Determine the most recent complete month across all the data (the current
   month may be partial -- exclude it from "missing" checks unless it's
   already well past the vendor's usual pay date within the month).
3. Flag MISSED BILLS: any vendor whose cadence is "monthly" (or biweekly/
   quarterly, adjusted for the cadence) and who has 4+ consecutive prior
   occurrences, but is ABSENT in the most recent expected month.
4. Flag AMOUNT ANOMALIES: any vendor's last_amount that is more than 2x the
   amount_stddev away from typical_amount (a spike or an unexpected drop).
5. Flag NEW RECURRING CANDIDATES: vendors with exactly 2-3 months present at
   a consistent amount and interval -- worth watching, not yet confirmed.
6. Write ONE self-contained HTML report to `{REPORTS_DIR}/synthesis_report.html`
   with three sections (Missed Bills, Amount Anomalies, New Recurring
   Candidates), each a table sorted by severity/dollar impact, plus a summary
   count at the top. No external assets, inline CSS only, must render
   correctly in a plain browser file:// open.
Reply with one sentence: how many flags of each kind you found.""",
)

ROLES[ "receipt_scanner" ] = AgentRole(
    name="receipt_scanner",
    output_path=f"{REPO_ROOT}/reports/orphaned_receipts_report.md",
    max_turns=60,
    system_prompt="""\
You are working ALONE, independent of the vendor-pattern team, on a
filesystem forensics task: finding receipt image files that are orphaned or
mislabeled inside a git repo that two other automated agents also write to
continuously, so expect some churn and don't assume every odd file is yours
to fix -- only report.

The naming convention used across this repo for receipt images is
`{vendor_slug}_{MM}_{DD}_{YY}_{dollars}_{cents}.jpg` (e.g.
"meijer_04_13_25_38_07.jpg" = Meijer, 04/13/25, $38.07), sometimes with an
"_item_N_" infix for itemized sub-receipts. This is a READ-ONLY reconnaissance
task: do not move, rename, or delete any file. Just report.""",
    task_prompt=f"""\
1. Recursively find every image file (jpg/jpeg/png) under these directories
   of {REPO_ROOT}: `readable_documents/receipts/`,
   `readable_documents/generated_spreadsheets/receipts/`,
   `receipt_parsing_tools/generated_spreadsheets/`,
   `tools/receipt_scanning_tools/incoming_scans/`.
2. For each file, parse its filename against the vendor/date/amount
   convention above. Classify each as:
   - CONFORMS: filename matches the convention cleanly.
   - MALFORMED: doesn't match (missing amount/date, garbled vendor, a raw
     scan_freezer_*/window_scan_* placeholder name that was never renamed by
     the intake pipeline, etc).
3. Connect to the expenses database (details below) and check whether each
   CONFORMS file's vendor+date+amount has a matching row in `expenses`
   (match loosely: same date, amount within $0.01, description contains a
   token from the vendor slug). Files with NO matching row are ORPHANED --
   a receipt image exists but nothing in the ledger points to it.
4. Also flag the reverse where cheap to check: any `expenses` row with a
   non-null `receipt_url` or `source_file` that points at a path which does
   not exist on disk (broken reference, not an orphan file but worth
   reporting alongside).
""" + DB_CONNECTION_BRIEFING + f"""

5. Write ONE markdown report to `{REPO_ROOT}/reports/orphaned_receipts_report.md`
   with sections: Summary (counts), Malformed Filenames (path list),
   Orphaned Images (path, best-guess vendor/date/amount parsed from the
   filename), Broken DB References (id_light, path referenced, table
   column). Sort each list by path.
Reply with one sentence: total files scanned and counts in each category.""",
)

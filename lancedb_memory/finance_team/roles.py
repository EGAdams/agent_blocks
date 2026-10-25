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
file under the repo except the single report you write in step 6 below.""",
    task_prompt=f"""\
1. Query EVERY row from `expenses` (all methods, not just one slice -- you
   are not one of the four method-sliced mapper agents).
2. Recursively find every image file (jpg/jpeg/png) under these directories
   of {REPO_ROOT}: {", ".join( f"`{d}`" for d in RECEIPT_DIRS )}.
3. Parse each filename against the vendor/date/amount convention above.
   Skip files that don't parse cleanly (that's receipt_scanner's job to
   flag as malformed, not yours) -- just build the pool of successfully
   parsed (vendor_slug, date, amount) receipts you can match against.
4. Classify each expense row as NO_RECEIPT_EXPECTED if its `description`
   matches a known receiptless pattern -- these row types structurally never
   get a photographed or scanned receipt, so treating them as accounting
   gaps is noise, not signal. Match case-insensitively, allowing minor
   punctuation/whitespace drift, against:
   - Internal / online transfers: e.g. "ONLINE TRANSFER", "TRANSFER TO",
     "TRANSFER FROM", "XFER".
   - Loan or commercial-line payments: e.g. "COMMRCL LN", "COMMERCIAL LN",
     "LOAN PMT", "LOAN PAYMENT", "PAID BY AUTO BILLPAYER".
   - Check clearings: description is "Check" (or "CHK") followed by a
     check number, e.g. "Check 5143".
   - Interest / finance charges: e.g. "INTEREST CHARGE", "FINANCE CHARGE",
     "PURCHASE INTEREST CHARGE".
   - Recurring-subscription vendors -- maintain this as an explicit,
     easily-extended list and match on description containing any of these
     tokens: "OPENAI", "AMAZON", "AMAZON MARKETPLACE", "AMZN DIGITAL",
     "PRIME VIDEO", "AUDIBLE", "APPLE.COM", "APPLE BILL", "AOL",
     "MYLIFE.COM". (Note in your report if you extend this list with a
     vendor you discover during the scan, and why.)
   Every row gets exactly one classification: NO_RECEIPT_EXPECTED, or
   eligible for the matching check in step 5. Record which pattern category
   matched for each NO_RECEIPT_EXPECTED row -- you need it for step 6.
5. For every expense row NOT classified as NO_RECEIPT_EXPECTED in step 4,
   look for ANY parsed receipt file whose date matches `expense_date`, whose
   amount is within $0.01 of `amount`, and whose vendor_slug shares a token
   with the row's normalized `description` (same loose-match rule
   receipt_scanner uses, applied in the opposite direction). If no such file
   exists anywhere in the four directories, the row is UNACCOUNTED. Rows
   classified NO_RECEIPT_EXPECTED in step 4 are never checked here and can
   never be UNACCOUNTED.
6. Write ONE markdown report to
   `{REPO_ROOT}/reports/unaccounted_expenses_report.md` (create the
   `reports/` directory if it doesn't exist -- do not create or touch any
   other file) with THREE sections:
   - Summary: total expense rows scanned, total parsed receipt files found
     across the four directories, count and % of rows classified
     NO_RECEIPT_EXPECTED (with a breakdown by pattern category from step 4),
     and count and % of the REMAINING (non-excluded) rows that are
     UNACCOUNTED, broken down by `method`.
   - Unaccounted Expenses: one table, sorted by `expense_date` descending,
     columns id_light, expense_date, amount, method, description
     (truncated to a reasonable length), and a short note on why no match
     was found (e.g. "no receipt file with matching date/amount/vendor in
     any of the four directories"). This table now contains ONLY rows that
     could plausibly have a receipt -- rows excluded in step 4 never appear
     here.
   - No Receipt Expected: one table, sorted by pattern category then
     `expense_date` descending, columns id_light, expense_date, amount,
     method, description (truncated), and which pattern category matched
     (transfer / loan-commercial-line / check / interest-finance-charge /
     recurring-subscription). This section exists so a reader can audit the
     exclusion logic instead of just trusting it.
   - A short caveats paragraph noting this is a loose text/date/amount
     match and may have false positives if a receipt filename is malformed
     (see receipt_scanner's report for those), a vendor token doesn't
     overlap the description wording, or a description matches a
     NO_RECEIPT_EXPECTED pattern coincidentally (e.g. a real vendor name
     containing "transfer").
""" + DB_CONNECTION_BRIEFING + """

Reply with one sentence: total expenses scanned, how many were classified
no-receipt-expected, and how many of the remainder were flagged as
unaccounted."""
)

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
   section and quote its headline numbers verbatim -- do not recompute or
   re-derive anything. If it has no summary, say so.
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


ROLES[ "category_spreadsheet" ] = AgentRole(
    name="category_spreadsheet",
    output_path=f"{REPO_ROOT}/readable_documents/reports",
    max_turns=8,
    allowed_tools=[ "Bash", "Read" ],
    system_prompt="""\
You are a spreadsheet-maker. You do not query the database or write files
yourself: you run ONE existing workflow (an ADW) that is code-only and
read-only, then report what it printed. Never edit any file or the database.""",
    task_prompt=f"""\
The user wants a spreadsheet of every expense in one category. The category
name (and optionally a year or an exact category id) is in the additional
instructions below. Run this from `{REPO_ROOT}`, substituting the name:

    cd {REPO_ROOT} && .venv/bin/python3 adws/adw_category_spreadsheet.py "<category name>" [--year YYYY] [--compare] [--category-id N]

Use --compare (with --year) whenever the user asks to compare with last year, spot
patterns, or find duplicates or mislabeled rows. The name "Medical" is a built-in
group: both Benefits & Medical parents (Robert and Rosemary) with all their sub-categories.

Read the last lines of its output:
- `DONE:` and `FILE:` -> reply with a few plain sentences: the category,
  how many expenses, the total, the file path from the `FILE:` line, the
  `COMPARE:` line (this year vs last) and each `FLAG:` line, saying the flags
  are things for a person to check, not corrections. Nothing was changed.
- `NEEDS_CHOICE:` -> the name did not single out one category. Do NOT pick
  one yourself and do NOT retry with a guessed id. Reply with the candidate
  names listed and ask which one the user means (an id may be passed back via
  `--category-id`).
- Anything else or a crash -> say it failed and quote the last error line.
If no category name was given, do not run anything; ask which category.""",
)


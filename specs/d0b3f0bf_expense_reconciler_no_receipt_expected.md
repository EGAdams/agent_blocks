# Plan: filter structurally-receiptless expenses out of `expense_reconciler`'s UNACCOUNTED bucket

## Problem

`lancedb_memory/finance_team/roles.py`, `ROLES['expense_reconciler']`, flags
83.7% of expenses as UNACCOUNTED against the real `rol_finances` DB. 79% of
those flags are `method = OTHER` bank-feed line items that structurally never
get a photographed/scanned receipt: internal transfers, loan/commercial-line
autopay, check clearings, interest charges, and recurring card subscriptions.
This buries the ~313 genuinely actionable CARD/CASH rows a human should
actually go find receipts for.

## Fix

Edit only the `task_prompt` (and, if needed, a one-line addition to
`system_prompt`) of `ROLES['expense_reconciler']` in
`lancedb_memory/finance_team/roles.py`. This is a pure prompt-text change —
`roles.py` is a data module (dataclass `AgentRole` holding prompt strings);
the classification logic runs inside the agent's own reasoning when it
executes the prompt, not in Python. **No new Python classes, functions, or
Protocols are needed for this change.** If in the course of implementing this
the builder decides some helper *does* need a Protocol/interface (it
shouldn't), the project has a hard rule: never suffix it `Port` — use the
`ISomeUsefulInterface` I-prefix convention (e.g. `IExpenseClassifier`).

Do not touch: `output_path`, `REPO_ROOT`, `RECEIPT_DIRS`,
`RECEIPT_NAMING_BRIEFING`, `DB_CONNECTION_BRIEFING`, `max_turns`,
`allowed_tools`, or the read-only / single-report-file constraints already
stated in `system_prompt`. Do not touch any other role in the file (mapper
roles, synthesizer, receipt_scanner).

## Exact edit

File: `lancedb_memory/finance_team/roles.py`, the `ROLES[ "expense_reconciler" ]
= AgentRole(...)` block (currently the last ~60 lines of the file, task_prompt
spanning what are today numbered steps 1–5).

Keep steps 1–3 of `task_prompt` verbatim (query all rows; find receipt image
files under `RECEIPT_DIRS`; parse filenames into `(vendor_slug, date,
amount)`, skipping malformed ones).

Insert a **new step 4** before the existing matching step, then renumber the
old step 4 to step 5 and the old step 5 (report) to step 6. Old step 4's
matching logic body is unchanged except it now only runs over the subset not
excluded in step 4.

New `task_prompt` body (replace the existing string with this shape — keep
the `RECEIPT_DIRS`/`REPO_ROOT` f-string interpolation exactly as it is today
for steps 1–3, and keep the `+ DB_CONNECTION_BRIEFING +` concatenation after
the report step exactly as it is today):

```
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
```

The `system_prompt` for this role already says "read-only ... do not
INSERT/UPDATE/DELETE ... do not create, move, rename, or delete any file
under the repo except the single report you write in step 5 below." Update
that trailing cross-reference from "step 5" to "step 6" since the write step
is now numbered 6.

Final sentence of `task_prompt` ("Reply with one sentence: total expenses
scanned and how many were flagged as unaccounted.") should be updated to also
mention the no-receipt-expected count, e.g.: "Reply with one sentence: total
expenses scanned, how many were classified no-receipt-expected, and how many
of the remainder were flagged as unaccounted."

## Verification

No automated tests cover `roles.py` (confirmed: no `test_*` files reference
`finance_team` or `expense_reconciler`). Verify with:

1. `cd lancedb_memory && .venv/bin/python -c "from finance_team.roles import ROLES; r = ROLES['expense_reconciler']; print(r.task_prompt)"` (or plain `python3 -c "..."` if no venv present in that dir — check first) to confirm the module still imports cleanly (valid Python string/f-string syntax) and print the rendered prompt for a final read-through.
2. Confirm `output_path`, `REPO_ROOT`, `RECEIPT_DIRS` usages in the block are byte-identical to before the edit (diff against git).
3. Confirm the new step 4 pattern list matches every example given in the
   prompt: "5/3 ONLINE TRANSFER TO CK", "5/3 COMMRCL LN #...PAID BY AUTO
   BILLPAYER", "Check 5143", "PURCHASE INTEREST CHARGE", OpenAI, Amazon
   Marketplace, Prime Video, Audible, Apple.com, AOL, MyLife.com — read each
   through the listed patterns/tokens by hand and confirm a match.
4. `git diff lancedb_memory/finance_team/roles.py` — confirm the diff touches
   only the `expense_reconciler` block (system_prompt's trailing
   cross-reference + task_prompt), nothing else in the file.
5. Do not run the agent against the live `rol_finances` DB as part of this
   change (that requires live DB credentials/network and is out of scope for
   verifying a prompt-text edit) — static review per steps 1-4 above is
   sufficient.

## Naming-convention note

This task does not require introducing any Protocol/interface class. If the
builder finds a reason to add one anyway (e.g. refactoring the pattern list
into a typed helper), it must use the `ISomeUsefulInterface` I-prefix
convention, never a `Port` suffix, per project rule.

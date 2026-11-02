# scala

The scanner clerk for everything that is not Mazda's. Added 2026-10-31 at EG's
request: Mazda handles finance scans, but electric bills, probate letters and
other paperwork were landing nowhere.

## How a scan reaches Scala

1. Someone presses Start Scan on the dashboard (Window or Freezer scanner).
2. The dashboard runs Mazda's intake classifier and stages a uniquely named
   copy in `~/rol_finances/tools/receipt_scanning_tools/incoming_scans/`,
   recording Mazda's verdict in `~/letta-code/dashboard/recent_report.json`.
   **Mazda goes first; none of her code changed.**
3. `scala-scanner.service` (`lancedb_memory/scala/watch.py`) sees the new file
   once it is 20 s old and runs `adws/adw_interface_scala_file_scan.py` on it.

## What the ADW decides

    check_seen -> check_mazda -> read_text -> classify -> file -> report

- Same image as before: nothing happens.
- Mazda called it a receipt or statement: left to her.
- Tesseract OCR + `scala/rules.py` at 0.90 or above: filed, no model.
- Otherwise Haiku (this folder's prompts, `claude-haiku-5-5`): "finance" goes
  back to Mazda; 0.70 or above and an allowed folder is filed; anything else
  waits in `scanned_documents/inbox/` for a person.

Filed copies go to `/mnt/c/Users/NewUser/scanned_documents/<folder>/`
(`C:\Users\NewUser\scanned_documents` on the Win11 box), named
`<scan date>_<kind>_<sha12>.<ext>`. New folders are created only under
`bills_511/` and `documents_511/`; `rol_receipts/` is never touched. The scan
in `incoming_scans/` is copied, never moved.

Every scan is one line in `~/scala/ledger.jsonl` (0600).

## By hand

    cd ~/agent_blocks
    lancedb_memory/.venv/bin/python adws/adw_interface_scala_file_scan.py "why" --scan <file> [--dry-run]
    lancedb_memory/.venv/bin/python adws/adw_interface_scala_file_scan.py "why" --scan <file> --folder bills_511/water

Tests: `cd lancedb_memory && .venv/bin/python -m scala.tests.test_scala`

Diagrams and an HTML manual are a separate job for a separate agent.

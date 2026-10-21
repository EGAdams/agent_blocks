#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Receipt Locate — find the real file behind a stale `receipt_url`.

`expense_review/receipt_opener.py` (rol_finances) refuses outright once its
one-shot exact lookup comes up empty or ambiguous -- a receipt filed as
`walgreens_01_22_25_9_85.jpg` that the row remembers as
`walgreens_01_23_25_9_85.jpg` (one day off; the scanner misread the date)
looks, from inside that single check, exactly like a receipt that was never
scanned at all. It almost always IS still on disk, just under a name a few
tokens off from what the row remembers, so refusing outright throws away a
search that filename-token overlap wins easily.

Phases:
* `search_candidates` (code) -- try the exact lookup first; most rows still
  resolve. Only on failure does it rank every file in the receipts tree by
  how much of the stored filename it shares.
* `select_match` (engineer) -- only when there is something to show: the
  human looks at the ranked list and says which one, if any, is right. No
  agent call earns its keep here -- which scrawled receipt is the real one
  is a one-glance human judgement, and getting it wrong writes a wrong
  filename back onto a live row with nothing left to flag the mistake.
* `finalize` (code) -- open the chosen file (or the one the exact lookup
  already found) and report the corrected `receipt_url` for the caller to
  persist.

Usage:
    uv run adws/adw_receipt_locate.py "<why this lookup is happening>" \\
        --receipt-url walgreens_01_23_25_9_85.jpg \\
        [--receipts-dir /path/to/receipts] [--auto-pick 1] \\
        [--message-out /tmp/line.txt] [--receipt-url-out /tmp/url.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(search_candidates) [-> engineer(select_match)] -> code(finalize)
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, receipt_locate, session, utils
from adw_modules.data_types import PhaseParams, ReceiptLocateRequest, ReceiptSelection

REQUIRED_AGENTS: list[str] = []      # entirely code + a human; no agent earns its keep here


def main(prompt: str, receipt_url: str, receipts_dir: str, auto_pick: str | None,
         message_out: str, receipt_url_out: str,
         config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)
    request = ReceiptLocateRequest(receipt_url=receipt_url, receipts_dir=receipts_dir)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Record which stored receipt_url could not be opened")) as ph:
        ph.log(input=prompt, receipt_url=receipt_url or "(none)")

    with run.phase(PhaseParams(name="search_candidates", kind="code", owner="receipt",
                               description="Try the exact lookup, then rank filename matches when it fails")) as ph:
        search = receipt_locate.search_candidates(run, request)
        ph.log(resolved_direct=search.resolved_direct, direct_path=search.direct_path or "(none)",
               candidates=len(search.candidates), reason=search.reason)

    selection = ReceiptSelection()
    if not search.resolved_direct and search.candidates:
        with run.phase(PhaseParams(name="select_match", kind="engineer", owner=run.engineer,
                                   description="Ask the human which ranked candidate, if any, is the real receipt")) as ph:
            print("\nThe stored receipt was not found. Close matches:")
            print(receipt_locate.format_choices(search))
            raw = auto_pick if auto_pick is not None else input("Pick a number, or Enter for none: ")
            selection = receipt_locate.parse_choice(raw, search)
            ph.log(raw=raw, picked=selection.picked,
                   chosen=(selection.candidate.relative if selection.candidate else "(none)"))

    with run.phase(PhaseParams(name="finalize", kind="code", owner="receipt",
                               description="Open whatever was found or chosen, and report the corrected receipt_url")) as ph:
        result = receipt_locate.finalize(run, request, search, selection)
        ph.log(message=result.message, receipt_url=result.receipt_url or "(unchanged)")

    if message_out:
        Path(message_out).write_text(result.message + "\n")
    if receipt_url_out:
        Path(receipt_url_out).write_text(result.receipt_url + "\n")

    accepted = bool(search.resolved_direct or selection.picked)
    return run.finish(accepted=accepted, reason=result.message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--receipt-url", default="", help="expenses.receipt_url as currently stored")
    parser.add_argument("--receipts-dir", default="",
                        help="override the receipts base dir (default: rol_finances' tree)")
    parser.add_argument("--auto-pick", default=None,
                        help="skip the interactive prompt; same answer a human would type "
                             "(tests, scripted runs)")
    parser.add_argument("--message-out", default="", help="write the operator's one line here")
    parser.add_argument("--receipt-url-out", default="",
                        help="write the corrected receipt_url here, empty when nothing changed")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.receipt_url, args.receipts_dir,
                  args.auto_pick, args.message_out, args.receipt_url_out,
                  args.config, args.adw_id))

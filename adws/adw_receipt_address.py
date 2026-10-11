#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pytesseract", "pillow"]
# ///
"""ADW Receipt Address — read a destination address directly off a receipt
image, OCR first and an agent only when OCR is not confident.

Built for the gap `adw_calculate_distance.py` leaves open: its own resolver
only ever runs when `validate_destination` first judges the expense's own
*words* illegible. A chain-vendor description like "Mister - Mister Leonard
car wash" (expense 468) reads as perfectly usable text -- "Mister" -- and
geocodes to the wrong branch 8 miles away, because nothing ever looked at
the receipt itself. This ADW is that missing look, standalone or as the
`--receipt` input to the other ADW's resolver.

Three phases, cheapest first:

* `ocr_text` (code) -- tesseract reads the receipt image's raw text. Free,
  local, no network, no agent call. Right most of the time for a printed
  receipt with the address near the top.
* `extract_address` (code) -- look for exactly one confident address-shaped
  line in that text (a house number plus a street suffix or quadrant tag).
  Zero matches or more than one both count as "not confident" rather than a
  guess -- same "too ambiguous to pick one silently" rule
  `DocumentDistanceCalculator` uses for an ambiguous vendor name.
* `resolve_fuzzy` (agent) -- only when OCR was not confident: a vision read
  of the receipt image itself, haiku by default (`--resolver locator`, same
  agent and the same `DestinationOutput`/`destination_locatable` contract
  `adw_calculate_distance.py`'s own resolver uses). Pass `--resolver ""` to
  skip this and get OCR's best (or empty) answer only.

Usage:
    uv run adws/adw_receipt_address.py "<why this address is being read>" \\
        --receipt-path /path/to/receipt.jpg \\
        [--resolver locator] [--message-out /tmp/line.txt] \\
        [--address-out /tmp/address.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(ocr_text) -> code(extract_address)
        [-> agent(resolve_fuzzy)] -> code(finalize)
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, gates, receipt_address, session, utils
from adw_modules.data_types import (AgentCall, DestinationOutput, PhaseParams,
                                    ReceiptAddressOptions, ReceiptAddressRequest)

REQUIRED_AGENTS: list[str] = []          # OCR-only by default; --resolver adds one
DEFAULT_RESOLVER = "locator"             # the cheap one; same agent the distance ADW uses


def main(options: ReceiptAddressOptions) -> int:
    cfg = agents.load_config(options.config)
    agents.validate(cfg, REQUIRED_AGENTS + ([options.resolver] if options.resolver else []))
    run = session.ensure(cfg, options.adw_id)
    request = ReceiptAddressRequest(receipt_path=options.receipt_path)

    dtn = "Record which receipt's address is being read and under what options"
    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer, description=dtn)) as ph:
        ph.log(input=options.prompt, receipt_path=options.receipt_path or "(none)",
              resolver=options.resolver or "(OCR only)")

    dtn = "Run tesseract over the receipt image; free, local, and right most of the time"
    with run.phase(PhaseParams(name="ocr_text", kind="code", owner="receipt", description=dtn)) as ph:
        text = receipt_address.ocr_text(run, request)
        ph.log(chars=len(text))

    dtn = "Look for one confident street-address line in the OCR text"
    with run.phase(PhaseParams(name="extract_address", kind="code", owner="receipt", description=dtn)) as ph:
        extraction = receipt_address.extract_address(text)
        ph.log(confident=extraction.confident, candidate=extraction.candidate or "(none)",
              reason=extraction.reason)

    resolved: DestinationOutput | None = None
    if not extraction.confident and options.resolver:
        dtn = "Read the address directly off the receipt image when the OCR pass found nothing confident"
        with run.phase(PhaseParams(name="resolve_fuzzy", kind="agent", owner=options.resolver,
                                   retries=1, description=dtn)) as ph:
            prompt = receipt_address.resolver_prompt(options.prompt, request, extraction)
            resolved = ph.call(AgentCall(output_type=DestinationOutput, prompt=prompt,
                                         gates=[gates.destination_locatable]))

    result = receipt_address.finalize(extraction, resolved, options.resolver)

    dtn = "Report the winning address and where it came from"
    with run.phase(PhaseParams(name="finalize", kind="code", owner="receipt", description=dtn)) as ph:
        ph.log(address=result.address or "(none)", source=result.source or "(none)",
              message=result.message)

    if options.message_out:
        Path(options.message_out).write_text(result.message + "\n")
    if options.address_out:
        Path(options.address_out).write_text(result.address + "\n")

    return run.finish(accepted=bool(result.address), reason=result.message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--receipt-path", default="", help="path to the receipt image to read")
    parser.add_argument("--resolver", default=DEFAULT_RESOLVER,
                        help=f"agent to read the receipt when OCR is not confident "
                             f"(default {DEFAULT_RESOLVER}; pass '' for OCR only)")
    parser.add_argument("--message-out", default="",
                        help="write the operator's one line here, for a non-terminal caller")
    parser.add_argument("--address-out", default="",
                        help="write the resolved address here, empty when nothing was found")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(ReceiptAddressOptions(
        prompt=utils.resolve_prompt(args.prompt), receipt_path=args.receipt_path,
        resolver=args.resolver, message_out=args.message_out, address_out=args.address_out,
        config=args.config, adw_id=args.adw_id,
    )))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Wait for Scan Completion -- has the requested document arrived, and what does it say?

Checks Scala's ledger for a scan logged after the request (or reads the file
EG names with --file, the manual fallback), confirms it was captured (OCR
found words), and lists the dollar amounts on it. It never picks one: the
amounts are presented, and `adw_liz_record_answer.py` records the one EG
confirms. Until then the workflow stays AWAITING_DOCUMENT_SCAN.

    reads:  scans
    writes: scans

    lancedb_memory/.venv/bin/python adws/adw_liz_wait_for_scan.py [--key gr_2025_payments] [--file /path/to/scan.pdf]
"""
from __future__ import annotations

import sys
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.models import ScanRequest, ScanState
from city_tax.state import WorkflowState

STEP = LizStep( "wait_for_scan", WorkflowState.AWAITING_DOCUMENT_SCAN, "Check whether the scan has arrived",
                reads=( "scans", ), writes=( "scans", ), checkpointed=False )


def _check( kit: Kit, handle ) -> None:
    scans = kit.papers.load( "scans" ) or {}
    if kit.args.key not in scans:
        kit.line = "I haven't asked for a scan, so there's nothing to wait for."
        return
    scan = ScanRequest.model_validate( scans[ kit.args.key ])
    if scan.state is not ScanState.WAITING:
        kit.line = f"That request is already { scan.state.value }."
        return
    path = Path( kit.args.file ).expanduser() if kit.args.file else None
    if path is not None and not path.is_file():
        kit.line = f"I can't find a file at { path }."
        return
    checked = kit.scan_desk().check( scan, path )
    scans[ scan.key ] = checked.model_dump( mode="json" )
    kit.save( "scans", scans )
    handle.log( state=checked.state.value, received=checked.received, amounts=checked.candidates )
    if checked.state is ScanState.RECEIVED:
        amounts = ", ".join( f"${ a }" for a in checked.candidates ) or "no dollar amounts"
        kit.line = ( f"Got it -- I read the scan ({ Path( checked.received ).name }). It shows { amounts }. Which is "
                     f"the 2025 Grand Rapids payment? Say \"Liz, the payment was $X\" -- I won't guess." )
    elif checked.received:
        kit.line = "A scan arrived, but I couldn't read any words on it. Could you scan it again, a little straighter?"
    else:
        kit.line = ( "I don't see a new scan yet. If you saved it somewhere else, tell me the file's path and I'll "
                     "read it from there." )


def add_args( parser ) -> None:
    parser.add_argument( "--key", default="gr_2025_payments" )
    parser.add_argument( "--file", default="", help="manual fallback: read this file instead of the scanner" )


def phases( kit: Kit ):
    return [( "check_scan", "Look for the scan and read it", _check )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

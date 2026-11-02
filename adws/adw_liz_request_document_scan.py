#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Request Document Scan -- ask EG to put one document in the scanner, then wait.

Says which document and why, puts the request on the company to-do list
(added by "liz", so Toyota and Liz both read it back), records the request
time -- the scan that arrives after it is the candidate -- and moves the
workflow to AWAITING_DOCUMENT_SCAN. Asking twice is refused: an open request
is repeated, not re-added.

    reads:  missing_items, scans
    writes: scans

    lancedb_memory/.venv/bin/python adws/adw_liz_request_document_scan.py --key gr_2025_payments
"""
from __future__ import annotations

import sys
from datetime import datetime

from liz_adw import Kit, LizStep, run_step
from city_tax.models import ScanRequest, ScanState
from city_tax.state import WorkflowState

STEP = LizStep( "request_document_scan", WorkflowState.AWAITING_DOCUMENT_SCAN, "Ask for a document scan",
                reads=( "missing_items", ), writes=( "scans", ), checkpointed=False )


def _ask( kit: Kit, handle ) -> None:
    scans = kit.papers.load( "scans" ) or {}
    existing = scans.get( kit.args.key )
    if existing and existing[ "state" ] == ScanState.WAITING.value:
        scan = ScanRequest.model_validate( existing )
        kit.flow.move( WorkflowState.AWAITING_DOCUMENT_SCAN, f"still waiting: { scan.key }" )
        kit.line = ( f"I'm still waiting on { scan.document } (to-do #{ scan.todo_number }). Put it in the scanner "
                     f"and tell me \"I've finished scanning it\" -- or \"there isn't one\"." )
        return
    item = next(( i for i in kit.need( "missing_items" ) if i[ "key" ] == kit.args.key and i[ "kind" ] == "scan" ), None )
    if item is None:
        kit.line = f"Nothing needs scanning for '{ kit.args.key }'."
        return
    scan = ScanRequest( key=item[ "key" ], document=item[ "document" ], reason=item[ "reason" ],
                        figure_key=item[ "figure_key" ], requested_at=datetime.now().astimezone())
    scan = kit.scan_desk().request( scan )
    scans[ scan.key ] = scan.model_dump( mode="json" )
    kit.save( "scans", scans )
    kit.flow.move( WorkflowState.AWAITING_DOCUMENT_SCAN, f"scan requested: { scan.key }" )
    handle.log( key=scan.key, todo=scan.todo_number )
    kit.line = ( f"I need { scan.document }. { scan.reason } Please place it in the scanner and tell me when you've "
                 f"finished scanning it -- or tell me there isn't one. (Company to-do #{ scan.todo_number }.)" )


def add_args( parser ) -> None:
    parser.add_argument( "--key", default="gr_2025_payments" )


def phases( kit: Kit ):
    return [( "ask", "Explain the document, ask for it on the to-do list, and wait", _ask )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

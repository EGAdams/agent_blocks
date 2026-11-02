#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Record Answer -- EG settles a missing figure (IHumanReview).

`--none` records that the document doesn't exist (the figure is $0, sourced
to EG's words); `--amount` records the figure EG confirms, from a scan or
from memory -- either way it is EG's answer, cited as such. The scan request
and its to-do item are closed, and every checkpoint that depended on the
figure is cleared, so "resume" recalculates from here and nothing earlier.

    reads:  scans
    writes: payments_2025 (or the named figure), scans, answers

    lancedb_memory/.venv/bin/python adws/adw_liz_record_answer.py --key gr_2025_payments --none --said "there weren't any"
    lancedb_memory/.venv/bin/python adws/adw_liz_record_answer.py --key gr_2025_payments --amount 400.00 --said "..."
"""
from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation

from liz_adw import Kit, LizStep, run_step
from city_tax.models import Figure, FigureStatus, ScanRequest, ScanState, SourceKind, SourceRef
from city_tax.state import WorkflowState

STEP = LizStep( "record_answer", WorkflowState.AWAITING_DOCUMENT_SCAN, "Record EG's answer",
                writes=( "payments_2025", "scans", "answers" ), checkpointed=False )

DOWNSTREAM = ( "resolve_taxable_income", "calculate_city_tax", "verify_tax_calculation", "populate_city_form",
               "validate_completed_pdf", "reconcile_returns", "generate_final_review_report", "publish_report_review" )


def _record( kit: Kit, handle ) -> None:
    key = kit.args.key
    if kit.args.none:
        amount, how = Decimal( "0" ), "none existed"
    else:
        try:
            amount = Decimal( kit.args.amount.replace( "$", "" ).replace( ",", "" ))
        except ( InvalidOperation, AttributeError ):
            kit.line = "I didn't catch an amount, so nothing was recorded."
            return
        how = "confirmed amount"
    now = datetime.now().astimezone()
    scans = kit.papers.load( "scans" ) or {}
    source = SourceRef( kind=SourceKind.HUMAN_ANSWER, where="EG, via Liz", line=key,
                        note=f"{ now:%Y-%m-%d %H:%M}: \"{ kit.args.said or how }\"" )
    refs = [ source ]
    if key in scans:
        scan = ScanRequest.model_validate( scans[ key ])
        if scan.received:
            refs.append( SourceRef( kind=SourceKind.TAXPAYER_DOCUMENT, where=scan.received, note="the scan EG confirmed" ))
        closed = kit.scan_desk().close( scan, ScanState.ANSWERED if kit.args.none else ScanState.RECEIVED,
                                        kit.args.said or how )
        scans[ key ] = closed.model_dump( mode="json" )
        kit.save( "scans", scans )
    figure_key = "payments_2025" if key == "gr_2025_payments" else key
    kit.save( figure_key, Figure( key=figure_key, label="2025 Grand Rapids estimated / extension payments and credit forward",
                                  amount=amount, status=FigureStatus.VERIFIED, sources=refs,
                                  note=f"EG: { kit.args.said or how }" ))
    answers = kit.papers.load( "answers" ) or []
    answers.append({ "at": now.isoformat(), "key": key, "amount": str( amount ), "said": kit.args.said })
    kit.save( "answers", answers )
    for step in DOWNSTREAM:
        kit.flow.checkpoints.pop( step, None )
    handle.log( key=key, amount=str( amount ), cleared=list( DOWNSTREAM ))
    kit.line = ( f"Recorded: 2025 Grand Rapids payments ${ amount:,.2f} ({ how }). Say \"resume the tax preparation\" "
                 f"and I'll recalculate from there." )


def add_args( parser ) -> None:
    parser.add_argument( "--key", default="gr_2025_payments" )
    parser.add_argument( "--none", action="store_true" )
    parser.add_argument( "--amount", default="" )
    parser.add_argument( "--said", default="", help="EG's words, kept as the source" )


def phases( kit: Kit ):
    return [( "record", "Record the answer as a sourced figure and clear dependent checkpoints", _record )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Await Human Review / Save Final Draft Package -- pause, or record EG's approval.

`--pause` parks the workflow (resume picks up where it stopped). `--approve`
is accepted only from AWAITING_REVIEW with no outstanding scan and a draft
whose status is "Ready for Approval"; it copies the draft PDF, both reports
and the workpapers into `~/liz_city_tax/final_package/<date>/` and marks the
workflow COMPLETED. Approval means "ready to sign" -- Liz never files the
return, submits it online, or pays anything.

    reads:  city_draft, pdf_fill, scans
    writes: final_package

    lancedb_memory/.venv/bin/python adws/adw_liz_record_review.py --pause | --approve --said "..."
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.models import ReturnStatus
from city_tax.state import WorkflowState
from city_tax.store import private_dir

STEP = LizStep( "record_review", WorkflowState.AWAITING_REVIEW, "Pause, or record the review decision",
                checkpointed=False )


def _decide( kit: Kit, handle ) -> None:
    if kit.args.pause:
        if kit.flow.state is WorkflowState.PAUSED:
            kit.line = "I'm already paused. Say \"resume the tax preparation\" when you're ready."
            return
        kit.flow.move( WorkflowState.PAUSED, kit.args.said or "paused by EG" )
        kit.line = "Paused. Everything finished so far is saved; say \"resume the tax preparation\" to pick up again."
        return
    draft = kit.papers.load( "city_draft" ) or {}
    waiting = any( s.get( "state" ) == "waiting" for s in ( kit.papers.load( "scans" ) or {}).values())
    if kit.flow.state is not WorkflowState.AWAITING_REVIEW or waiting or \
            draft.get( "status" ) != ReturnStatus.READY_FOR_APPROVAL.value:
        kit.line = ( f"I can't mark it approved yet: the draft is \"{ draft.get( 'status', 'not prepared' )}\""
                     + ( " and a scan is outstanding" if waiting else "" ) + ". The open questions come first." )
        return
    folder = private_dir( kit.home / "final_package" / datetime.now().strftime( "%Y-%m-%d_%H%M%S" ))
    fill = kit.need( "pdf_fill" )
    for source in ( Path( fill[ "draft" ]), kit.home / "reports" / "comparison.html", kit.home / "reports" / "review.html" ):
        if source.exists():
            shutil.copy2( source, folder / source.name )
    shutil.copytree( kit.home / "work", folder / "workpapers" )
    kit.save( "final_package", { "folder": str( folder ), "approved_by": "EG", "said": kit.args.said,
                                 "at": datetime.now().astimezone().isoformat()})
    kit.flow.move( WorkflowState.COMPLETED, "approved by EG" )
    handle.log( folder=str( folder ))
    kit.line = ( f"Saved the approved draft package in { folder.name }. It is ready to sign and file by hand -- "
                 f"I have not filed or paid anything." )


def add_args( parser ) -> None:
    group = parser.add_mutually_exclusive_group( required=True )
    group.add_argument( "--pause", action="store_true" )
    group.add_argument( "--approve", action="store_true" )
    parser.add_argument( "--said", default="" )


def phases( kit: Kit ):
    return [( "decide", "Pause, or check and record approval", _decide )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

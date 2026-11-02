#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Prepare Grand Rapids 2025 City Taxes -- the whole workflow, one small ADW at a time.

    Initialize / Resume Liz
    +-- Acquire 2025 City Forms ........ locate_official_forms, download_form
    +-- Read and compare the packets ... read_tax_pdf x2, extract_pdf_fields x2, compare_form_fields, compare_instructions
    +-- Locate 2024 City Return ........ locate_city_return_2024
    +-- Verify Filing / Residency ...... resolve_residency, check_filing_status
    +-- Publish Comparison Report #1 ... generate_difference_report, publish_report --which comparison
    +-- Read the 2025 Federal Return ... locate_federal_return, read_federal_figures
    +-- Read Supporting Finance Data ... lookup_finance
    +-- Resolve Missing Information .... resolve_missing, request_document_scan   (pauses here)
    +-- Apply Rules / Calculate ........ resolve_taxable_income, calculate_city_tax, verify_tax_calculation
    +-- Populate Official City PDF ..... populate_city_form, validate_completed_pdf
    +-- Reconcile ...................... reconcile_returns
    +-- Publish Report #2 .............. generate_final_review_report, publish_report --which review
    +-- Await Human Review

Each step is its own ADW subprocess with its own SSSF session and log, so a
failure names one step and only that step reruns. Checkpointed steps skip
themselves. With a scan outstanding the workflow stops at
AWAITING_DOCUMENT_SCAN -- unless `--draft-while-waiting`, which carries on
with the missing line left blank and the status "Needs Documents". It never
files or pays anything.

    lancedb_memory/.venv/bin/python adws/adw_liz_prepare_city_taxes.py [--draft-while-waiting] [--redo-from STEP]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.models import ScanState
from city_tax.state import WorkflowState, describe

STEP = LizStep( "prepare_city_taxes", WorkflowState.IDLE, "Prepare the 2025 Grand Rapids city return",
                checkpointed=False )
HERE = Path( __file__ ).resolve().parent

#: ( checkpoint name, script, extra args, needs pypdf )
PLAN: list[ tuple[ str, str, list[ str ], bool ]] = [
    ( "locate_official_forms",       "adw_liz_locate_official_forms.py",       [], False ),
    ( "download_forms_needed",       "adw_liz_download_form.py",               [], False ),
    ( "read_tax_pdf_2024",           "adw_liz_read_tax_pdf.py",                [ "--year", "2024" ], False ),
    ( "read_tax_pdf_2025",           "adw_liz_read_tax_pdf.py",                [ "--year", "2025" ], False ),
    ( "extract_pdf_fields_2025",     "adw_liz_extract_pdf_fields.py",          [ "--which", "2025" ], True ),
    ( "extract_pdf_fields_prior",    "adw_liz_extract_pdf_fields.py",          [ "--which", "prior" ], True ),
    ( "compare_form_fields",         "adw_liz_compare_form_fields.py",         [], False ),
    ( "compare_instructions",        "adw_liz_compare_instructions.py",        [], False ),
    ( "locate_city_return_2024",     "adw_liz_locate_city_return_2024.py",     [], False ),
    ( "resolve_residency",           "adw_liz_resolve_residency.py",           [], False ),
    ( "check_filing_status",         "adw_liz_check_filing_status.py",         [], False ),
    ( "generate_difference_report",  "adw_liz_generate_difference_report.py",  [], False ),
    ( "publish_report_comparison",   "adw_liz_publish_report.py",              [ "--which", "comparison" ], False ),
    ( "locate_federal_return",       "adw_liz_locate_federal_return.py",       [], False ),
    ( "read_federal_figures",        "adw_liz_read_federal_figures.py",        [], False ),
    ( "lookup_finance",              "adw_liz_lookup_finance.py",              [], False ),
    ( "resolve_missing",             "adw_liz_resolve_missing.py",             [], False ),
    ( "request_document_scan",       "adw_liz_request_document_scan.py",       [], False ),
    ( "resolve_taxable_income",      "adw_liz_resolve_taxable_income.py",      [], False ),
    ( "calculate_city_tax",          "adw_liz_calculate_city_tax.py",          [], False ),
    ( "verify_tax_calculation",      "adw_liz_verify_tax_calculation.py",      [], False ),
    ( "populate_city_form",          "adw_liz_populate_city_form.py",          [], True ),
    ( "validate_completed_pdf",      "adw_liz_validate_completed_pdf.py",      [], True ),
    ( "reconcile_returns",           "adw_liz_reconcile_returns.py",           [], False ),
    ( "generate_final_review_report", "adw_liz_generate_final_review_report.py", [], False ),
    ( "publish_report_review",       "adw_liz_publish_report.py",              [ "--which", "review" ], False ),
]
FIRST_AFTER_SCAN = "resolve_taxable_income"


def command( script: str, extra: list[ str ], needs_pdf: bool, force: bool, line_file: str ) -> list[ str ]:
    tail = [ *extra, *([ "--force" ] if force else []), "--message-out", line_file ]
    if needs_pdf:
        uv = shutil.which( "uv" ) or "uv"
        return [ uv, "run", "--quiet", "--script", str( HERE / script ), *tail ]
    return [ sys.executable, str( HERE / script ), *tail ]


def scan_open( kit: Kit ) -> bool:
    scans = kit.papers.load( "scans" ) or {}
    return any( s.get( "state" ) == ScanState.WAITING.value for s in scans.values())


def _run_plan( kit: Kit, handle ) -> None:
    lines: list[ str ] = []
    redo = kit.args.redo_from
    forcing = False
    for name, script, extra, needs_pdf in PLAN:
        forcing = forcing or ( redo == name )
        if name == FIRST_AFTER_SCAN and scan_open( kit ) and not kit.args.draft_while_waiting:
            kit.flow = kit.store.load()
            kit.line = ( " ".join( lines[ -2: ]) + " I'm pausing here until the document is scanned or you tell me "
                         "it doesn't exist." ).strip()
            handle.log( stopped_at=name, reason="scan outstanding" )
            return
        with tempfile.TemporaryDirectory() as scratch:
            line_file = str( Path( scratch ) / "line.txt" )
            done = subprocess.run( command( script, extra, needs_pdf, forcing, line_file ), cwd=HERE.parent,
                                   capture_output=True, text=True, timeout=900 )
            said = Path( line_file ).read_text().strip() if Path( line_file ).exists() else ""
        handle.log( step=name, exit=done.returncode, said=said[ :200 ])
        if not said.startswith( "Already done" ):
            lines.append( said )
        if done.returncode != 0:
            kit.flow = kit.store.load()
            kit.line = f"I stopped at '{ name }': { said or done.stderr.strip().splitlines()[ -1:] }. Fix that and say resume."
            return
    kit.flow = kit.store.load()
    waiting = scan_open( kit )
    kit.flow.move( WorkflowState.AWAITING_DOCUMENT_SCAN if waiting else WorkflowState.AWAITING_REVIEW,
                   "draft prepared" )
    published = kit.papers.load( "published_review" ) or {}
    kit.line = ( f"The draft is ready for your review on the ADW Generated tab ({ published.get( 'file', 'review report' )})."
                 + ( " It still needs the scanned document before the numbers are final." if waiting else "" )
                 + " Nothing has been filed or paid." )


def add_args( parser ) -> None:
    parser.add_argument( "--draft-while-waiting", action="store_true",
                         help="with a scan outstanding, still calculate a draft with that line blank" )
    parser.add_argument( "--redo-from", default="", help="force this checkpointed step and every later one" )


def _initialize( kit: Kit, handle ) -> None:
    before = kit.flow.state
    if before in ( WorkflowState.PAUSED, WorkflowState.FAILED ):
        kit.flow.resume()
        kit.store.save( kit.flow )
    handle.log( before=before.value, now=describe( kit.flow ))


def phases( kit: Kit ):
    return [( "initialize", "Load Liz's workflow state; resume a pause or a failed step", _initialize ),
            ( "run_steps", "Run each city-tax ADW in order, skipping finished ones", _run_plan )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

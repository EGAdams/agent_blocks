#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Generate Final Review Report -- Report #2, "Completed Draft and Federal Reconciliation".

The draft line by line with its sources, the federal-to-city table, the
2024-to-2025 table, every check, assumptions, outstanding questions and the
workflow status. The draft PDF itself stays private (it carries names and the
home address); the page says where it is and that Liz can open it.

    reads:  city_draft, calc_checks, reconciliation, federal_figures, pdf_fill, pdf_checks
    writes: report_review_html

    lancedb_memory/.venv/bin/python adws/adw_liz_generate_final_review_report.py [--force]
"""
from __future__ import annotations

import sys
from datetime import datetime

from liz_adw import Kit, LizStep, run_step, write_text_private
from city_tax.models import CheckResult, CityReturnDraft, Figure
from city_tax.reports import review_report
from city_tax.state import WorkflowState

STEP = LizStep( "generate_final_review_report", WorkflowState.PUBLISHING_REPORT, "Write the review report",
                reads=( "city_draft", "calc_checks", "reconciliation", "federal_figures", "pdf_fill", "pdf_checks" ),
                writes=( "report_review_html", ))


def _render( kit: Kit, handle ) -> None:
    draft = CityReturnDraft.model_validate( kit.need( "city_draft" ))
    checks = [ CheckResult.model_validate( c ) for c in kit.need( "calc_checks" )]
    pdf_checks = [ CheckResult.model_validate( c ) for c in kit.papers.load( "pdf_checks" ) or []]
    federal = kit.need( "federal_figures" )
    fill = kit.papers.load( "pdf_fill" ) or {}
    status = ( "The 2025 federal return is not filed (on hold for a CPA/EA); federal figures come from "
               f"{ federal[ 'source' ]} and are provisional." )
    pdf_note = ( f"Draft filled on the official 2025 fillable form: { fill.get( 'draft', '(not yet made)' ).split( '/' )[ -1 ]}, "
                 f"kept privately in Liz's folder on the Win11 machine (not on the dashboard, since it carries "
                 f"names and the home address). Ask Liz \"show me the completed city tax form\" to open it. SSNs, bank "
                 f"numbers and signatures are blank by design. Nothing has been filed or paid." )
    html = review_report( draft, checks, kit.need( "reconciliation" ),
                          [ Figure.model_validate( f ) for f in federal[ "figures" ]], status, pdf_note, pdf_checks,
                          datetime.now())
    path = write_text_private( kit.home / "reports" / "review.html", html )
    line = lambda n: draft.line( n ).column_c if draft.line( n ) else None
    summary = ( f"status { draft.status.value }; taxable ${ line( '17' ):,.0f}, tax ${ line( '18b' ):,.0f}, "
                f"payments { '$' + format( line( '19d' ), ',.0f' ) if line( '19d' ) is not None else 'unknown' }" )
    kit.save( "report_review_html", { "path": str( path ), "summary": summary })
    handle.log( path=str( path ), status=draft.status.value )
    kit.line = f"Wrote the 2025 review report ({ summary })."


def phases( kit: Kit ):
    return [( "render", "Render the review page", _render )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

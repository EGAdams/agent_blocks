#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Publish Report to SPA -- puts one of Liz's pages on ADW Generated.

Copies the private page into the ADW Generated folder under a dated name and
adds it to the top of the existing link index (`HtmlLinkIndex`, the same one
every other ADW uses). The two reports publish independently.

    reads:  report_<which>_html
    writes: published_<which>

    lancedb_memory/.venv/bin/python adws/adw_liz_publish_report.py --which comparison|review [--force]
"""
from __future__ import annotations

import sys
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.state import WorkflowState

STEP = LizStep( "publish_report", WorkflowState.PUBLISHING_REPORT, "Publish a report to ADW Generated",
                reads=( "report_<which>_html", ), writes=( "published_<which>", ), suffix_arg="which" )

TITLES = { "comparison": ( "Grand_Rapids_City_Taxes_2024_vs_2025_Form_Comparison",
                           "Grand Rapids City Taxes -- 2024 vs. 2025 Form Comparison" ),
           "review":     ( "Grand_Rapids_2025_Draft_and_Federal_Reconciliation",
                           "Grand Rapids 2025 -- Completed Draft and Federal Reconciliation" )}


def _publish( kit: Kit, handle ) -> None:
    which = kit.args.which
    report = kit.need( f"report_{ which }_html" )
    stem, title = TITLES[ which ]
    summary = f"{ title } -- { report.get( 'summary', '' ) or 'by Liz' }"
    name = kit.publisher().publish( Path( report[ "path" ]).read_text( encoding="utf-8" ), stem, summary )
    kit.save( f"published_{ which }", { "file": name, "url": f"/adw_generated/{ name }", "title": title })
    handle.log( file=name )
    kit.line = f"Published \"{ title }\" on the ADW Generated tab ({ name })."


def add_args( parser ) -> None:
    parser.add_argument( "--which", required=True, choices=sorted( TITLES ))


def phases( kit: Kit ):
    return [( "publish", "Write the page and list it first on ADW Generated", _publish )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

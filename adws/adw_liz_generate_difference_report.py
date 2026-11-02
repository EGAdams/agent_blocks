#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Generate Difference Report -- Report #1, "2024 vs. 2025 Form Comparison".

Renders the comparison for a reviewer who is not a programmer: the bottom
line, a side-by-side table, what may affect Robert and Rosemary, notes,
unresolved questions, and links to every official source. The page is kept
privately; `adw_liz_publish_report.py --which comparison` publishes it.

    reads:  form_differences, field_differences, forms_downloaded, city_return_2024 (optional)
    writes: report_comparison_html

    lancedb_memory/.venv/bin/python adws/adw_liz_generate_difference_report.py [--force]
"""
from __future__ import annotations

import sys
from datetime import datetime

from liz_adw import Kit, LizStep, run_step
from city_tax.models import FormDifference, OfficialForm
from city_tax.reports import comparison_report
from city_tax.state import WorkflowState

STEP = LizStep( "generate_difference_report", WorkflowState.PUBLISHING_REPORT, "Write the form comparison report",
                reads=( "form_differences", "field_differences", "forms_downloaded" ),
                writes=( "report_comparison_html", ))


def _render( kit: Kit, handle ) -> None:
    diffs = [ FormDifference.model_validate( d ) for d in kit.need( "form_differences" )]
    diffs += [ FormDifference.model_validate( d ) for d in kit.papers.load( "field_differences" ) or []]
    forms = [ OfficialForm.model_validate( f ) for f in kit.need( "forms_downloaded" )]
    city = kit.papers.load( "city_return_2024" ) or {}
    notes = [ "Compared: the City's official 2025 resident packet (live) and its 2024 resident packet (the "
              "Internet Archive's copy of the URL the city published for 2024).",
              "The 2024 return Robert and Rosemary filed (prepared in tax software) shows Deaf and Disabled boxes; "
              "the city's own 2024 blank form does not. That is the software's form, not a city change.",
              "Pages without a readable text layer were read by OCR, so a few line labels differ only by OCR "
              "spelling; those are shown as cosmetic." ] + list( city.get( "notes", []))
    questions = [ "Residency is taken from the 2024 resident return and the unchanged home address; the city's "
                  "street-directory check needs a person (the county site refuses automated requests).",
                  "No Grand Rapids extension (GR-4868) or 2025 estimated payment was found among the documents. "
                  "A federal extension does not extend the city return." ]
    html = comparison_report( diffs, forms, notes, questions, datetime.now())
    path = kit.home / "reports" / "comparison.html"
    from liz_adw import write_text_private
    write_text_private( path, html )
    kit.save( "report_comparison_html", { "path": str( path ), "differences": len( diffs )})
    handle.log( path=str( path ), bytes=len( html ))
    kit.line = f"Wrote the 2024 vs 2025 comparison report ({ len( diffs )} items)."


def phases( kit: Kit ):
    return [( "render", "Render the comparison page", _render )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

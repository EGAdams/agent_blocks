#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Locate Federal Return -- is there a completed 2025 federal return to read?

Searches the family's tax folders and the Windows Downloads/Documents folders
for a 2025 federal return (FreeTaxUSA PDF, 1040). A candidate counts as final
only if its text says it was filed or accepted. With none, Liz says so and
the next step reads the plan's settled figures, labelled provisional.

    reads:  ~/rol_finances, /mnt/c/Users/NewUser/{Downloads,Documents}
    writes: federal_return_search

    lancedb_memory/.venv/bin/python adws/adw_liz_locate_federal_return.py [--force]
"""
from __future__ import annotations

import re
import subprocess
import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.federal import FederalReturnFinder
from city_tax.state import WorkflowState

STEP = LizStep( "locate_federal_return", WorkflowState.READING_FEDERAL_RETURN, "Look for the filed 2025 federal return",
                writes=( "federal_return_search", ))
_FILED = re.compile( r"e-?file (?:was )?accepted|electronically filed|submission id|accepted by the irs", re.I )


def _search( kit: Kit, handle ) -> None:
    candidates = FederalReturnFinder().locate( 2025 )
    final = []
    for doc in candidates:
        try:
            text = subprocess.run([ "pdftotext", "-l", "3", doc.path, "-" ], capture_output=True, text=True,
                                  timeout=60 ).stdout
        except ( OSError, subprocess.TimeoutExpired ):
            text = ""
        if _FILED.search( text ):
            final.append( doc.path )
    kit.save( "federal_return_search", { "candidates": candidates, "final": final })
    handle.log( candidates=len( candidates ), final=len( final ))
    kit.line = ( f"Found a filed 2025 federal return: { final[ 0 ]}." if final else
                 f"No filed 2025 federal return was found ({ len( candidates )} draft/candidate file(s)). The plan "
                 f"says filing is on hold for a CPA/EA, so I'll use the plan's settled figures and mark them provisional." )


def phases( kit: Kit ):
    return [( "search", "Search for a filed 2025 federal return", _search )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Read Federal Schedule -- the 2025 federal figures the city return starts from.

With no filed federal return, the figures come from the family's newest
published tax plan page (`PlanPageFederalRepository`): church pay per pastor
and its parts, housing allowance actually paid, Social Security, the SE
health insurance deduction, bank interest. Each keeps its source and is
PROVISIONAL (or VERIFIED, for third-party statements); a figure the page
doesn't state is MISSING. A filed return, once it exists, replaces this
repository at the composition root below -- nothing downstream changes.

    reads:  federal_return_search, the newest Barnes_2025_Tax_Plan_*.html on ADW Generated
    writes: federal_figures

    lancedb_memory/.venv/bin/python adws/adw_liz_read_federal_figures.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.federal import PlanPageFederalRepository
from city_tax.models import FigureStatus
from city_tax.state import WorkflowState

STEP = LizStep( "read_federal_figures", WorkflowState.READING_FEDERAL_RETURN, "Read the 2025 federal figures",
                reads=( "federal_return_search", ), writes=( "federal_figures", ))


def _read( kit: Kit, handle ) -> None:
    search = kit.need( "federal_return_search" )
    if search.get( "final" ):
        raise RuntimeError( "A filed federal return exists; wire a repository that reads it before continuing." )
    repo = PlanPageFederalRepository()
    figures = repo.figures( 2025 )
    if not figures:
        raise RuntimeError( "No published tax plan page was found to read figures from." )
    kit.save( "federal_figures", { "source": repo.describe(), "filed_return": False, "figures": figures })
    missing = [ f.label for f in figures if f.status is FigureStatus.MISSING ]
    pay = { f.key: f.amount for f in figures if f.key.startswith( "church_pay_" )}
    handle.log( figures=len( figures ), missing=missing )
    kit.line = ( f"Read { len( figures )} federal figures from { repo.describe()}: church pay Robert "
                 f"${ pay.get( 'church_pay_robert' ) or 0:,.2f}, Rosemary ${ pay.get( 'church_pay_rosemary' ) or 0:,.2f} "
                 f"(provisional until the federal return is filed)."
                 + ( f" Missing: { '; '.join( missing )}." if missing else "" ))


def phases( kit: Kit ):
    return [( "read_figures", "Read and source every federal figure", _read )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

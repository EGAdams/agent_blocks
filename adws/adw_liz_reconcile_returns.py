#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Reconcile Federal and City Returns -- every adjustment, and every year-over-year change.

Two tables: (A) each federal figure and what the city does with it, (B) the
2024 filed city return against this draft, line by line, with the reason a
change came from income and not from the rules (the comparison report showed
the rules unchanged). The return is never forced to agree with anything.

    reads:  city_draft, federal_figures, city_return_2024, finance_evidence (optional)
    writes: reconciliation

    lancedb_memory/.venv/bin/python adws/adw_liz_reconcile_returns.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.calculator import reconcile
from city_tax.models import CityReturnDraft, Figure
from city_tax.state import WorkflowState

STEP = LizStep( "reconcile_returns", WorkflowState.RECONCILING, "Reconcile federal, city and last year",
                reads=( "city_draft", "federal_figures", "city_return_2024" ), writes=( "reconciliation", ))


def _reconcile( kit: Kit, handle ) -> None:
    draft = CityReturnDraft.model_validate( kit.need( "city_draft" ))
    federal = [ Figure.model_validate( f ) for f in kit.need( "federal_figures" )[ "figures" ]]
    last = [ Figure.model_validate( f ) for f in kit.need( "city_return_2024" )[ "figures" ]]
    tables = reconcile( draft, federal, last )
    tables[ "finance_evidence" ] = kit.papers.load( "finance_evidence" ) or {}
    kit.save( "reconciliation", tables )
    handle.log( federal_rows=len( tables[ "federal_to_city" ]), year_rows=len( tables[ "year_over_year" ]))
    kit.line = tables[ "why" ] or "Reconciled the draft against the federal figures and last year's return."


def phases( kit: Kit ):
    return [( "reconcile", "Build the federal-to-city and 2024-to-2025 tables", _reconcile )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

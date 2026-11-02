#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Verify Tax Calculation -- redo the arithmetic independently and check every line.

    reads:  city_draft, rules_2025
    writes: calc_checks

    lancedb_memory/.venv/bin/python adws/adw_liz_verify_tax_calculation.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.calculator import books_check, parts_check, verify
from city_tax.models import CityReturnDraft, CityTaxRules, Figure
from city_tax.state import WorkflowState

STEP = LizStep( "verify_tax_calculation", WorkflowState.CALCULATING, "Check the calculation",
                reads=( "city_draft", "rules_2025", "federal_figures", "finance_evidence" ), writes=( "calc_checks", ))


def _verify( kit: Kit, handle ) -> None:
    checks = verify( CityReturnDraft.model_validate( kit.need( "city_draft" )),
                     CityTaxRules.model_validate( kit.need( "rules_2025" )))
    figures = [ Figure.model_validate( f ) for f in kit.need( "federal_figures" )[ "figures" ]]
    checks += parts_check( figures )
    checks += books_check( figures, ( kit.papers.load( "finance_evidence" ) or {}).get( "rows", []))
    kit.save( "calc_checks", checks )
    failed = [ c for c in checks if not c.passed ]
    handle.log( checks=len( checks ), open=[ c.name for c in failed ])
    kit.line = ( f"All { len( checks )} arithmetic checks pass." if not failed else
                 f"{ len( checks ) - len( failed )} of { len( checks )} checks pass; open: "
                 + "; ".join( f"{ c.name } ({ c.detail })" for c in failed ) + "." )


def phases( kit: Kit ):
    return [( "verify", "Recompute each line from the rules and compare", _verify )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

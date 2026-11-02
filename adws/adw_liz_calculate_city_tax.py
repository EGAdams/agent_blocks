#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Calculate City Tax -- the 2025 Grand Rapids resident return, line by line.

Applies the 2025 rules read from the city's own 2025 instructions (rate,
exemption amount) to the sorted inputs. Decimal arithmetic, whole dollars
rounded half up as the form says. A blank input leaves its line and every
total after it blank, and the draft's status says why.

    reads:  city_inputs, facts_2025
    writes: city_draft

    lancedb_memory/.venv/bin/python adws/adw_liz_calculate_city_tax.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.calculator import GrandRapidsResidentCalculator
from city_tax.models import Figure, Residency
from city_tax.rules import FactsRuleProvider
from city_tax.state import WorkflowState

STEP = LizStep( "calculate_city_tax", WorkflowState.CALCULATING, "Calculate the city return",
                reads=( "city_inputs", "facts_2025" ), writes=( "city_draft", ))


def _calculate( kit: Kit, handle ) -> None:
    inputs = kit.need( "city_inputs" )
    rules = FactsRuleProvider({ 2025: kit.need( "facts_2025" )}).rules( 2025 )
    draft = GrandRapidsResidentCalculator().calculate(
        [ Figure.model_validate( f ) for f in inputs[ "taxed" ]], rules, Residency( inputs[ "residency" ]),
        int( inputs[ "exemptions" ]), Figure.model_validate( inputs[ "payments" ]))
    kit.save( "city_draft", draft )
    kit.save( "rules_2025", rules )
    line = lambda n: draft.line( n ).column_c if draft.line( n ) else None
    handle.log( status=draft.status.value, taxable=str( line( "17" )), tax=str( line( "18b" )))
    owed = line( "21" )
    kit.line = ( f"2025 city return: taxable income ${ line( '17' ):,.0f}, tax ${ line( '18b' ):,.0f}"
                 + ( f", amount owed ${ owed:,.0f}" if owed else "" if owed is None else ", nothing owed" )
                 + ( "" if line( "19d" ) is not None else ", payments still unknown" )
                 + f". Status: { draft.status.value }." )


def phases( kit: Kit ):
    return [( "calculate", "Apply the 2025 rules to the inputs", _calculate )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

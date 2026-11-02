#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Resolve Taxable Income -- which federal figures the city taxes, and which it doesn't.

Assembles the calculator's inputs: the residency and exemption count, the
federal figures sorted into "taxed by the city" (church pay, interest) and
"not taxed / not allowed" (Social Security, housing allowance, the SE health
insurance deduction), and the 2025 payments figure -- MISSING unless EG has
settled it. Nothing is assumed: a blank stays blank.

    reads:  federal_figures, residency, payments_2025 (optional)
    writes: city_inputs

    lancedb_memory/.venv/bin/python adws/adw_liz_resolve_taxable_income.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.models import Figure
from city_tax.state import WorkflowState

STEP = LizStep( "resolve_taxable_income", WorkflowState.CALCULATING, "Sort the federal figures for the city",
                reads=( "federal_figures", "residency" ), writes=( "city_inputs", ))

TAXED = ( "church_pay_robert", "church_pay_rosemary", "taxable_interest" )
NOT_TAXED = { "social_security_robert": "The city does not tax Social Security.",
              "social_security_rosemary": "The city does not tax Social Security.",
              "housing_allowance_paid": "Excluded from federal income tax; the city follows the federal figure.",
              "se_health_insurance_robert": "A federal adjustment the city does not allow -- so it is not subtracted.",
              "se_health_insurance_rosemary": "A federal adjustment the city does not allow -- so it is not subtracted." }


def _sort( kit: Kit, handle ) -> None:
    federal = [ Figure.model_validate( f ) for f in kit.need( "federal_figures" )[ "figures" ]]
    residency = kit.need( "residency" )
    payments = kit.papers.load( "payments_2025" ) or Figure(
        key="payments_2025", label="2025 Grand Rapids estimated / extension payments and credit forward",
        note="Not settled: no document found and EG hasn't answered yet." ).model_dump( mode="json" )
    taxed = [ f for f in federal if f.key in TAXED ]
    excluded = [{ "figure": f, "why": NOT_TAXED[ f.key ]} for f in federal if f.key in NOT_TAXED ]
    kit.save( "city_inputs", { "residency": residency[ "residency" ], "exemptions": residency[ "exemptions" ],
                               "taxed": taxed, "excluded": excluded, "payments": payments })
    handle.log( taxed=[ f.key for f in taxed ], excluded=len( excluded ), payments=payments.get( "amount" ))
    total = sum(( f.amount for f in taxed if f.amount is not None ), start=0 )
    kit.line = ( f"The city taxes church pay and interest (${ total:,.2f} before rounding); Social Security, the "
                 f"housing allowance and the SE health insurance deduction stay off the city return." )


def phases( kit: Kit ):
    return [( "sort_figures", "Sort each federal figure into taxed or not, with the reason", _sort )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

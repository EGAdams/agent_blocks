#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Resolve Residency -- which Grand Rapids return applies for 2025.

Never from a ZIP code. Resident only when the prior city return was filed
on the resident form AND the family's records carry the same home address
into the year. Also counts exemptions from birth dates (regular + 65 or over).

    reads:  the 2024 return image (OCR), the family's plan and tax skill
    writes: residency

    lancedb_memory/.venv/bin/python adws/adw_liz_resolve_residency.py [--force]
"""
from __future__ import annotations

import sys
from decimal import Decimal

from liz_adw import Kit, LizStep, run_step
from city_tax.documents import TAX_DOCUMENTS
from city_tax.rules import DocumentResidencyResolver, birth_dates, exemptions_for
from city_tax.state import WorkflowState

STEP = LizStep( "resolve_residency", WorkflowState.VERIFYING_STATUS, "Decide residency and exemptions",
                reads=( "city_return_2024", "facts_2024" ), writes=( "residency", ))


def _resolve( kit: Kit, handle ) -> None:
    image = next(( TAX_DOCUMENTS / "CF-1040_grand_rapids_page_1" ).glob( "*.jp*g" ), None )
    text = kit.ocr().read( image ) if image else ""
    last = { f[ "key" ]: f[ "amount" ] for f in ( kit.papers.load( "city_return_2024" ) or {}).get( "figures", [])}
    rate = ( kit.papers.load( "facts_2024" ) or {}).get( "resident_rate" )
    as_dec = lambda v: Decimal( str( v )) if v is not None else None
    residency, refs, caveat = DocumentResidencyResolver(
        text, str( image or "" ), prior_tax=as_dec( last.get( "gr2024_tax" )),
        prior_taxable=as_dec( last.get( "gr2024_taxable" )), prior_resident_rate=as_dec( rate )).resolve( 2025 )
    births, birth_ref = birth_dates()
    count = exemptions_for( births, 2025, [ "Regular", "65 or over", "Blind" ])
    kit.save( "residency", { "residency": residency.value, "sources": refs, "caveat": caveat,
                             "exemptions": count, "exemption_source": birth_ref,
                             "people": sorted( births )})
    handle.log( residency=residency.value, exemptions=count )
    kit.line = ( f"2025 residency: { residency.value.replace( '_', ' ' )}, from the 2024 resident return and the "
                 f"unchanged home address. Exemptions: { count } (each is 65 or over). { caveat }" )


def phases( kit: Kit ):
    return [( "resolve", "Weigh the residency evidence and count exemptions", _resolve )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

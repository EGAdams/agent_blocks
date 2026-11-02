#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Locate 2024 City Return -- find, classify, and read last year's Grand Rapids return.

Classifies every folder under tax_documents (official blank form, completed
return, federal supporting, city schedule, payment/extension, other). Then
reads the completed 2024 CF-1040 off its image and its payments worksheet,
and keeps the figures only if the page's own arithmetic closes. If the
return isn't there, that is recorded and nothing is invented.

    reads:  ~/rol_finances/readable_documents/tax_documents/
    writes: documents_2024, city_return_2024

    lancedb_memory/.venv/bin/python adws/adw_liz_locate_city_return_2024.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.documents import City2024ReturnReader, TaxDocumentFolders
from city_tax.models import DocumentClass
from city_tax.state import WorkflowState

STEP = LizStep( "locate_city_return_2024", WorkflowState.VERIFYING_STATUS, "Find and read the 2024 city return",
                writes=( "documents_2024", "city_return_2024" ))


def _classify( kit: Kit, handle ) -> None:
    docs = TaxDocumentFolders().locate( 0 )
    kit.save( "documents_2024", docs )
    counts: dict[ str, int ] = {}
    for doc in docs:
        counts[ doc.doc_class.value ] = counts.get( doc.doc_class.value, 0 ) + 1
    kit._cache[ "returns" ] = [ d for d in docs if d.doc_class is DocumentClass.COMPLETED_TAXPAYER_RETURN ]
    handle.log( **counts )


def _read( kit: Kit, handle ) -> None:
    figures, notes = City2024ReturnReader( kit.ocr()).read()
    kit.save( "city_return_2024", { "figures": figures, "notes": notes,
                                    "found": [ d.path for d in kit._cache[ "returns" ]]})
    handle.log( figures=len( figures ), notes=notes )
    if figures:
        tax = next( f.amount for f in figures if f.key == "gr2024_tax" )
        refund = next( f.amount for f in figures if f.key == "gr2024_refund" )
        kit.line = ( f"Found the 2024 Grand Rapids return and its figures check out: city tax ${ tax:,.0f}, "
                     f"refund ${ refund:,.0f}." + ( " " + " ".join( notes ) if notes else "" ))
    else:
        kit.line = " ".join( notes ) or "The 2024 Grand Rapids return could not be read."


def phases( kit: Kit ):
    return [( "classify_documents", "Classify every saved tax document", _classify ),
            ( "read_2024_return", "Read the 2024 return and check its arithmetic", _read )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Verify Filing and Extension Status -- has the 2025 city return been filed or extended?

Looks for evidence among the saved documents (a filed 2025 city return, a
GR-4868 extension, 2025 estimated payment records) and reads the deadline
from the city's own 2025 instructions. A federal extension does not extend
the city return (the instructions say so), and is never treated as one.

    reads:  documents_2024 (all saved documents), facts_2025
    writes: filing_status

    lancedb_memory/.venv/bin/python adws/adw_liz_check_filing_status.py [--force]
"""
from __future__ import annotations

import sys
from datetime import date

from liz_adw import Kit, LizStep, run_step
from city_tax.models import DocumentClass, LocatedDocument
from city_tax.state import WorkflowState

STEP = LizStep( "check_filing_status", WorkflowState.VERIFYING_STATUS, "Check filing and extension status",
                reads=( "documents_2024", "facts_2025" ), writes=( "filing_status", ))


def _evidence( kit: Kit, handle ) -> None:
    docs = [ LocatedDocument.model_validate( d ) for d in kit.need( "documents_2024" )]
    city_2025 = [ d.path for d in docs if d.tax_year == 2025 and d.doc_class in
                  ( DocumentClass.COMPLETED_TAXPAYER_RETURN, DocumentClass.PAYMENT_OR_EXTENSION )]
    kit._cache[ "city_2025" ] = city_2025
    handle.log( city_2025_documents=len( city_2025 ))


def _deadline( kit: Kit, handle ) -> None:
    facts = kit.need( "facts_2025" )
    due = date.fromisoformat( facts[ "due_date" ]) if facts.get( "due_date" ) else None
    today = date.today()
    found = kit._cache[ "city_2025" ]
    status = {
        "due_date": str( due ) if due else None,
        "past_due": bool( due and today > due ),
        "filed_return_found": False,
        "city_extension_found": False,
        "city_payments_found": bool( found ),
        "documents": found,
        "federal_extension_note": "A federal extension was filed; the city's instructions say that does not "
                                  "extend the Grand Rapids return (GR-4868 is required).",
        "source": facts.get( "source" ),
    }
    kit.save( "filing_status", status )
    handle.log( **{ k: v for k, v in status.items() if k != "documents" })
    late = f"was due { due:%B %-d, %Y} and is past due" if status[ "past_due" ] else f"is due { due:%B %-d, %Y}"
    kit.line = ( f"The 2025 Grand Rapids return { late }. No filed city return, no city extension and no 2025 "
                 f"city estimated payment records were found -- that needs confirming, and I've listed it as "
                 f"a document to ask for." )


def phases( kit: Kit ):
    return [( "find_evidence", "Look for a filed return, extension or payments", _evidence ),
            ( "deadline", "Read the deadline from the 2025 instructions and compare with today", _deadline )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

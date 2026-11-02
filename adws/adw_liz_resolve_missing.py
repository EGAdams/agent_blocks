#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Resolve Missing Information -- what the return still lacks, and how to get each piece.

Lists every input the city return needs that isn't settled, and says for
each whether it is a paper document (Liz asks for a scan) or a question (Liz
asks EG). A document already among the saved tax documents is never asked
for again. The 2025 payments figure becomes a `payments_2025` workpaper the
moment it is settled -- by a scan EG confirms, or by EG saying there were none.

    reads:  federal_figures, filing_status, documents_2024, payments_2025 (if any), scans (if any)
    writes: missing_items

    lancedb_memory/.venv/bin/python adws/adw_liz_resolve_missing.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.state import WorkflowState

STEP = LizStep( "resolve_missing", WorkflowState.READING_FINANCE, "List what the return still needs",
                reads=( "federal_figures", "filing_status" ), writes=( "missing_items", ), checkpointed=False )

SCANNABLE = {
    "gr_2025_payments": {
        "document": "any 2025 Grand Rapids estimated-payment, extension-payment or credit-forward record "
                    "(GR-1040ES voucher, cancelled check, bank line or city receipt)",
        "reason": "Line 19b of the city return. None is among the saved documents, and the 2024 return shows "
                  "estimates were paid that year, so 2025 may have some too.",
        "figure_key": "payments_2025",
    },
}


def missing( kit: Kit ) -> list[ dict ]:
    items: list[ dict ] = []
    status = kit.need( "filing_status" )
    if kit.papers.load( "payments_2025" ) is None and not status.get( "city_payments_found" ):
        items.append({ "key": "gr_2025_payments", "kind": "scan", **SCANNABLE[ "gr_2025_payments" ]})
    for figure in kit.need( "federal_figures" )[ "figures" ]:
        if figure[ "status" ] == "missing" and figure[ "key" ].startswith( "church_pay" ):
            items.append({ "key": figure[ "key" ], "kind": "question", "document": figure[ "label" ],
                           "reason": figure.get( "note", "" ), "figure_key": figure[ "key" ]})
    items.append({ "key": "robert_interest", "kind": "question",
                   "document": "Any 1099-INT or 1099-DIV issued under Robert's SSN for 2025",
                   "reason": "The IRS transcript read so far covers Rosemary's SSN only; interest is taxable to the city.",
                   "figure_key": "taxable_interest", "blocking": False })
    return items


def _list( kit: Kit, handle ) -> None:
    items = missing( kit )
    kit.save( "missing_items", items )
    blocking = [ i for i in items if i.get( "blocking", True )]
    handle.log( missing=len( items ), blocking=len( blocking ))
    if not items:
        kit.line = "Nothing is missing for the city return."
    else:
        kit.line = ( f"Still needed: " + "; ".join( i[ "document" ] for i in items )
                     + f". { len( blocking )} of them hold(s) up the final numbers." )


def phases( kit: Kit ):
    return [( "list_missing", "List each unsettled input and how to get it", _list )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

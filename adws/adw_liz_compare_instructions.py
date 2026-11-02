#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Compare Tax Instructions -- the 2024 and 2025 official resident packets.

Reads each year's facts out of its own text (rates, exemption amount and
boxes, due date, exempt income, refused federal adjustments, line labels,
sentences) and classifies every difference as substantive, cosmetic or
uncertain. Wording alone is never reported as a change in the law.

    reads:  pages_2024, pages_2025
    writes: facts_2024, facts_2025, form_differences

    lancedb_memory/.venv/bin/python adws/adw_liz_compare_instructions.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.compare import FactsComparator, read_year
from city_tax.models import ChangeKind
from city_tax.state import WorkflowState

STEP = LizStep( "compare_instructions", WorkflowState.COMPARING_FORMS, "Compare the 2024 and 2025 instructions",
                reads=( "pages_2024", "pages_2025" ), writes=( "facts_2024", "facts_2025", "form_differences" ))


def _read_facts( kit: Kit, handle ) -> None:
    for year in ( 2024, 2025 ):
        packet = kit.need( f"pages_{ year }" )
        facts = read_year( packet[ "pages" ], year, packet[ "source" ], packet.get( "ocr_pages" ))
        kit.save( f"facts_{ year }", facts )
        handle.log( year=year, rate=str( facts.resident_rate ), exemption=str( facts.exemption_amount ),
                    boxes=facts.exemption_boxes, lines=len( facts.form_lines ), sentences=len( facts.sentences ))


def _compare( kit: Kit, handle ) -> None:
    diffs = FactsComparator().compare( kit.need( "facts_2024" ), kit.need( "facts_2025" ))
    kit.save( "form_differences", diffs )
    counts = { kind: sum( 1 for d in diffs if d.kind is kind ) for kind in ChangeKind }
    handle.log( **{ k.value: v for k, v in counts.items()})
    kit.line = ( f"Compared the 2024 and 2025 instructions: { counts[ ChangeKind.SUBSTANTIVE ]} substantive, "
                 f"{ counts[ ChangeKind.COSMETIC ]} cosmetic or unchanged, { counts[ ChangeKind.UNCERTAIN ]} for a "
                 f"person to read." )


def phases( kit: Kit ):
    return [( "read_facts", "Read each year's rules and lines out of its own text", _read_facts ),
            ( "compare", "Classify every difference", _compare )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

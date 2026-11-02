#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Locate Official Grand Rapids Forms.

Reads the City of Grand Rapids' Individual Taxpayers page and lists every
individual-return PDF it links, with year, residency, kind and "fillable"
read from the link itself. The city only keeps the current packet, so the
2024 instructions are found through the Internet Archive's copy of the URL
the city published for 2024 -- the URL recorded on the 2024 return's own
saved extract -- and marked archived.

    reads:  the city page (live), tax_documents/CF-1040_grand_rapids_page_1/*_Summary.json
    writes: forms_catalog

    lancedb_memory/.venv/bin/python adws/adw_liz_locate_official_forms.py [--force] [--message-out F]
"""
from __future__ import annotations

import json
import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.documents import TAX_DOCUMENTS
from city_tax.forms import CITY_PAGE, CityFormsPage, WaybackForms
from city_tax.models import FormKind
from city_tax.state import WorkflowState

STEP = LizStep( "locate_official_forms", WorkflowState.ACQUIRING_FORMS,
                "Find the official Grand Rapids individual forms", writes=( "forms_catalog", ))


def _list_city( kit: Kit, handle ) -> None:
    forms = CityFormsPage().list_forms()
    kit._cache[ "forms" ] = forms
    handle.log( page=CITY_PAGE, forms=len( forms ))


def _find_2024_packet( kit: Kit, handle ) -> None:
    original = ""
    for summary in ( TAX_DOCUMENTS / "CF-1040_grand_rapids_page_1" ).glob( "*_Summary.json" ):
        try:
            original = json.loads( summary.read_text( encoding="utf-8" )).get( "source", "" )
        except ( OSError, ValueError ):
            continue
    archived = WaybackForms().find( original, "2024 Resident Forms and Instructions (CF-1040, as the city "
                                    "published it for 2024)", 2024, FormKind.INSTRUCTIONS ) if original else None
    if archived:
        kit._cache[ "forms" ].append( archived )
    handle.log( original=original, archived=archived.url if archived else "(none)" )


def _save( kit: Kit, handle ) -> None:
    forms = kit._cache[ "forms" ]
    kit.save( "forms_catalog", forms )
    current = [ f for f in forms if f.tax_year == 2025 and f.residency == "resident" ]
    kit.line = ( f"Found { len( forms )} official forms on the city's page"
                 f"{ ' plus the archived 2024 packet' if any( f.archived for f in forms ) else '' }; "
                 f"{ len( current )} are 2025 resident forms." )


def phases( kit: Kit ):
    return [( "list_city_page", "Read the city's Individual Taxpayers page for form links", _list_city ),
            ( "find_2024_packet", "Find the Internet Archive's copy of the 2024 resident packet", _find_2024_packet ),
            ( "save_catalog", "Save the catalog workpaper", _save )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

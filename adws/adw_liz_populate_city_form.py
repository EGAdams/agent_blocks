#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pypdf"]
# ///
"""ADW Liz: Populate City Form -- fill the official 2025 GR-1040R fillable PDF as a DRAFT copy.

Fills the city's own fillable form (never an imitation): names, the
exemption schedule, every income and tax line, payments if known. SSNs,
bank routing/account numbers and signatures are deliberately left blank --
a person adds them when the return is approved. The blank official form is
never touched; the draft is a new file in `~/liz_city_tax/drafts/` (0600),
outside every web root. Run with `uv run --script` (pypdf).

    reads:  city_draft, forms_downloaded, the family's tax skill (names, birth dates, address)
    writes: pdf_fill   ({ draft, values })

    uv run --script adws/adw_liz_populate_city_form.py [--force]
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.calculator import CityFormFieldMapper
from city_tax.models import CityReturnDraft, FormKind, OfficialForm
from city_tax.rules import TAX_SKILL
from city_tax.state import WorkflowState
from city_tax.store import private_dir

STEP = LizStep( "populate_city_form", WorkflowState.POPULATING_PDF, "Fill the official 2025 form as a draft",
                reads=( "city_draft", "forms_downloaded" ), writes=( "pdf_fill", ))


def identity_fields( skill: Path = TAX_SKILL ) -> dict[ str, str ]:
    """Names, birth dates and street address from the family's records -- never SSNs or bank numbers."""
    try:
        text = skill.read_text( encoding="utf-8" )
    except OSError:
        return {}
    out: dict[ str, str ] = {}
    people = re.findall( r"(Robert|Rosemary) ([A-Z])\. Barnes \(b\. (\d{4})-(\d\d)-(\d\d)\)", text )
    for first, initial, year, month, day in people:
        if first == "Robert":
            out.update({ "Taxpayer's First Name": "ROBERT", "INITIAL 1": initial, "Taxpayer's Last Name": "BARNES",
                         "Date1_af_date": f"{ month }/{ day }/{ year }" })
        else:
            out.update({ "IF JOINT RETURN SPOUSES FIRST NAME": "ROSEMARY", "INITIAL_2": initial, "LAST NAME": "BARNES",
                         "Date2_af_date": f"{ month }/{ day }/{ year }" })
    if m := re.search( r"(\d{3,5} [A-Z][a-z]+ (?:NW|NE|SW|SE)),? (Grand Rapids),? (MI) (495\d\d)", text ):
        out.update({ "PRESENT HOME ADDRESS NUMBER AND STREET": m.group( 1 ).upper(),
                     "CITY TOWN OR POST OFFICE STATE": m.group( 2 ).upper(), "State": m.group( 3 ),
                     "ZIP CODE": m.group( 4 )})
    return out


def fill( blank: Path, values: dict[ str, str ], out: Path ) -> list[ str ]:
    """IPdfFormWriter over pypdf. Returns the names it could not find."""
    import pypdf
    from pypdf.generic import NameObject

    reader = pypdf.PdfReader( str( blank ))
    writer = pypdf.PdfWriter()
    writer.append( reader )
    seen: set[ str ] = set()
    for page in writer.pages:
        texts: dict[ str, str ] = {}
        for annot in page.get( "/Annots" ) or []:
            widget = annot.get_object()
            parts, node, kind = [], widget, None
            while node is not None:
                node = node.get_object()
                if node.get( "/T" ) is not None:
                    parts.append( str( node[ "/T" ]))
                kind = kind or node.get( "/FT" )
                node = node.get( "/Parent" )
            name = ".".join( reversed( parts ))
            if name not in values:
                continue
            seen.add( name )
            if kind == "/Btn":
                states = [ str( k ) for k in ( widget.get( "/AP" ) or {}).get( "/N", {}).keys() if str( k ) != "/Off" ]
                want = values[ name ]
                on = want if want in states else ( states[ 0 ] if states and want not in ( "", "/Off" ) and
                                                   not want.startswith( "/" ) else None )
                widget[ NameObject( "/AS" )] = NameObject( on or "/Off" )
                target = widget.get( "/Parent" ).get_object() if widget.get( "/Parent" ) is not None else widget
                if on:
                    target[ NameObject( "/V" )] = NameObject( on )
            else:
                texts[ name ] = values[ name ]
        if texts:
            writer.update_page_form_field_values( page, texts, auto_regenerate=False )
    writer.set_need_appearances_writer( True )
    # The official form recalculates totals with its own scripts, which print 0 for a payment we don't know yet.
    # The draft carries Liz's verified arithmetic instead, so its calculation order is dropped (the blank
    # official form on disk is untouched).
    acro = writer._root_object.get( "/AcroForm" )
    if acro is not None and "/CO" in acro.get_object():
        del acro.get_object()[ "/CO" ]
    private_dir( out.parent )
    with open( out, "wb" ) as handle:
        writer.write( handle )
    out.chmod( 0o600 )
    return sorted( set( values ) - seen )


def _fill( kit: Kit, handle ) -> None:
    draft = CityReturnDraft.model_validate( kit.need( "city_draft" ))
    forms = [ OfficialForm.model_validate( f ) for f in kit.need( "forms_downloaded" )]
    blank = next( f for f in forms if f.tax_year == 2025 and f.fillable and f.kind is FormKind.RETURN
                  and f.residency == "resident" )
    values = { **identity_fields(), **CityFormFieldMapper().fields( draft )}
    out = kit.home / "drafts" / f"GR-1040R_2025_Barnes_DRAFT_{ datetime.now():%Y%m%d_%H%M%S}.pdf"
    unmatched = fill( Path( blank.local_path ), values, out )
    kit.save( "pdf_fill", { "draft": str( out ), "blank": blank.local_path, "blank_sha256": blank.sha256,
                            "values": values, "unmatched": unmatched, "status": draft.status.value })
    kit.outputs.append( str( out ))
    handle.log( draft=str( out ), fields=len( values ), unmatched=unmatched )
    kit.line = ( f"Filled { len( values ) - len( unmatched )} fields on the official 2025 form as a draft "
                 f"({ out.name }). SSNs, bank numbers and signatures are left for a person."
                 + ( f" Not found on the form: { ', '.join( unmatched )}." if unmatched else "" ))


def phases( kit: Kit ):
    return [( "fill_form", "Fill the official fillable PDF into a new draft file", _fill )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

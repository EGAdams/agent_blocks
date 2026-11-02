#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Download Form -- one official form, or the set the 2025 return needs.

`--key` names one catalog entry. With no key it downloads what the return
and the comparison need: the 2025 resident fillable return, the 2025
resident packet (instructions), the 2025 extension form, the prior-year
resident fillable common form, and the archived 2024 resident packet.
Files land in `~/liz_city_tax/forms/official/<year>/`, never overwriting:
different bytes get a new name. Each is recorded with URL, sha256, time.

    reads:  forms_catalog
    writes: forms_downloaded

    lancedb_memory/.venv/bin/python adws/adw_liz_download_form.py [--key 2025-resident-return-fillable] [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.forms import CityFormsPage, WaybackForms
from city_tax.models import FormKind, OfficialForm
from city_tax.state import WorkflowState

STEP = LizStep( "download_forms", WorkflowState.ACQUIRING_FORMS, "Download the official forms",
                reads=( "forms_catalog", ), writes=( "forms_downloaded", ), suffix_arg="key" )


def wanted( form: OfficialForm ) -> bool:
    if form.archived:
        return True
    if form.tax_year == 2025 and form.residency == "resident" and form.kind in ( FormKind.RETURN, FormKind.INSTRUCTIONS ):
        return True
    if form.tax_year == 2025 and form.kind is FormKind.EXTENSION:
        return True
    return form.tax_year == 0 and form.residency == "resident" and form.fillable


def _download( kit: Kit, handle ) -> None:
    catalog = [ OfficialForm.model_validate( f ) for f in kit.need( "forms_catalog" )]
    chosen = [ f for f in catalog if ( f.key == kit.args.key if kit.args.key != "needed" else wanted( f ))]
    if not chosen:
        raise RuntimeError( f"No catalog form matches '{ kit.args.key }'." )
    have = { f[ "key" ]: f for f in ( kit.papers.load( "forms_downloaded" ) or [])}
    for form in chosen:
        folder = kit.home / "forms" / "official" / str( form.tax_year or "prior" )
        source = WaybackForms() if form.archived else CityFormsPage()
        got = source.download( form, folder )
        have[ got.key ] = got.model_dump( mode="json" )
        handle.log( key=got.key, path=got.local_path, sha256=got.sha256[ :12 ])
    kit.save( "forms_downloaded", list( have.values()))
    kit.line = f"Downloaded { len( chosen )} official form(s): " + ", ".join( f.title for f in chosen ) + "."


def add_args( parser ) -> None:
    parser.add_argument( "--key", default="needed", help="a forms_catalog key; default: the set the return needs" )


def phases( kit: Kit ):
    return [( "download", "Download and record each chosen form", _download )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

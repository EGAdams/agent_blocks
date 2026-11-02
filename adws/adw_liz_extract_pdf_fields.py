#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pypdf"]
# ///
"""ADW Liz: Extract PDF Fields -- every fillable field on an official form.

Name, type, page, position, tooltip and the "on" states of each box, read
with pypdf. Run with `uv run --script` (pypdf is not in the receptionist
venv); the orchestrator does that.

    reads:  forms_downloaded
    writes: fields_<which>   (which: 2025 = the 2025 resident fillable; prior = the prior-year common form)

    uv run --script adws/adw_liz_extract_pdf_fields.py --which 2025 [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.models import FormKind, OfficialForm
from city_tax.state import WorkflowState

STEP = LizStep( "extract_pdf_fields", WorkflowState.COMPARING_FORMS, "List an official form's fillable fields",
                reads=( "forms_downloaded", ), writes=( "fields_<which>", ), suffix_arg="which" )


def form_for( forms: list[ OfficialForm ], which: str ) -> OfficialForm:
    year = 0 if which == "prior" else int( which )
    for form in forms:
        if form.tax_year == year and form.residency == "resident" and form.fillable and form.kind is FormKind.RETURN:
            return form
    raise RuntimeError( f"No fillable { which } resident return has been downloaded." )


def read_fields( path: str ) -> list[ dict ]:
    import pypdf
    reader = pypdf.PdfReader( path )
    fields: list[ dict ] = []
    for number, page in enumerate( reader.pages, start=1 ):
        for annot in page.get( "/Annots" ) or []:
            widget = annot.get_object()
            if widget.get( "/Subtype" ) != "/Widget":
                continue
            parts, node, kind, tip = [], widget, None, ""
            while node is not None:
                node = node.get_object()
                if node.get( "/T" ) is not None:
                    parts.append( str( node[ "/T" ]))
                kind = kind or node.get( "/FT" )
                tip = tip or str( node.get( "/TU" ) or "" )
                node = node.get( "/Parent" )
            states = [ str( k ) for k in ( widget.get( "/AP" ) or {}).get( "/N", {}).keys()] if kind == "/Btn" else []
            fields.append({ "name": ".".join( reversed( parts )), "type": str( kind ), "page": number,
                            "rect": [ round( float( x )) for x in widget[ "/Rect" ]], "tooltip": tip,
                            "states": [ s for s in states if s != "/Off" ]})
    return fields


def _extract( kit: Kit, handle ) -> None:
    form = form_for([ OfficialForm.model_validate( f ) for f in kit.need( "forms_downloaded" )], kit.args.which )
    fields = read_fields( form.local_path )
    kit.save( f"fields_{ kit.args.which }", { "source": form.url, "fields": fields })
    handle.log( fields=len( fields ))
    kit.line = f"The { kit.args.which } resident form has { len( fields )} fillable fields."


def add_args( parser ) -> None:
    parser.add_argument( "--which", required=True, choices=[ "2025", "prior" ])


def phases( kit: Kit ):
    return [( "extract_fields", "Read every widget's name, type, page and position", _extract )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Compare Tax Form Fields -- the 2025 fillable return vs the prior-year fillable form.

Field-by-field, on the return's own pages: fields only one form has, and
fields that moved. A field is identified by its name; a moved rectangle is
layout (cosmetic), a field that exists in one form only is UNCERTAIN until a
person looks -- a new box can be a new rule or just a new widget.

    reads:  fields_2025, fields_prior
    writes: field_differences

    lancedb_memory/.venv/bin/python adws/adw_liz_compare_form_fields.py [--force]
"""
from __future__ import annotations

import sys

from liz_adw import Kit, LizStep, run_step
from city_tax.models import ChangeKind, FormDifference, SourceKind, SourceRef
from city_tax.state import WorkflowState

STEP = LizStep( "compare_form_fields", WorkflowState.COMPARING_FORMS, "Compare the two forms' fields",
                reads=( "fields_2025", "fields_prior" ), writes=( "field_differences", ))


def compare_fields( before: dict, after: dict ) -> list[ FormDifference ]:
    refs = [ SourceRef( kind=SourceKind.OFFICIAL_FORM, where=before[ "source" ]),
             SourceRef( kind=SourceKind.OFFICIAL_FORM, where=after[ "source" ])]
    # the 2025 packet puts the return after the instructions; compare the return pages only
    a = { f[ "name" ]: f for f in before[ "fields" ]}
    b = { f[ "name" ]: f for f in after[ "fields" ]}
    out: list[ FormDifference ] = []
    for name in sorted( set( a ) - set( b )):
        out.append( FormDifference( area="Form fields", item=name, before=a[ name ][ "tooltip" ] or a[ name ][ "type" ],
                                    kind=ChangeKind.UNCERTAIN, sources=refs,
                                    explanation="On the prior-year fillable form only." ))
    for name in sorted( set( b ) - set( a )):
        out.append( FormDifference( area="Form fields", item=name, after=b[ name ][ "tooltip" ] or b[ name ][ "type" ],
                                    kind=ChangeKind.UNCERTAIN, sources=refs,
                                    explanation="On the 2025 fillable form only." ))
    same = set( a ) & set( b )
    moved = [ n for n in same if a[ n ][ "rect" ] != b[ n ][ "rect" ]]
    out.append( FormDifference( area="Form fields", item=f"{ len( same )} fields in both forms",
                                before=f"{ len( a )} fields", after=f"{ len( b )} fields", kind=ChangeKind.COSMETIC,
                                sources=refs, explanation=( f"Same names; { len( moved )} moved on the page." if moved
                                                            else "Same names in the same places." )))
    return out


def _compare( kit: Kit, handle ) -> None:
    diffs = compare_fields( kit.need( "fields_prior" ), kit.need( "fields_2025" ))
    kit.save( "field_differences", diffs )
    only = [ d for d in diffs if d.kind is ChangeKind.UNCERTAIN ]
    handle.log( differences=len( diffs ), one_sided=len( only ))
    kit.line = f"Compared the fillable forms: { diffs[ -1 ].item }; { len( only )} field(s) appear in only one."


def phases( kit: Kit ):
    return [( "compare_fields", "Compare field names and positions", _compare )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

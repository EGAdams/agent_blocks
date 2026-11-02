#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pypdf"]
# ///
"""ADW Liz: Validate Completed PDF -- does the draft say what the workpapers say, legibly?

Reads every field back out of the draft and compares it with what was meant
to be written; checks each value fits its box (no clipping); checks the
pages are all there and render (pdftoppm); and checks the blank official
form on disk is byte-for-byte unchanged (sha256).

    reads:  pdf_fill, city_draft
    writes: pdf_checks

    uv run --script adws/adw_liz_validate_completed_pdf.py [--force]
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.models import CheckResult
from city_tax.state import WorkflowState

STEP = LizStep( "validate_completed_pdf", WorkflowState.POPULATING_PDF, "Check the filled PDF",
                reads=( "pdf_fill", ), writes=( "pdf_checks", ))


def _validate( kit: Kit, handle ) -> None:
    import pypdf
    fill = kit.need( "pdf_fill" )
    draft, blank = Path( fill[ "draft" ]), Path( fill[ "blank" ])
    checks: list[ CheckResult ] = []
    reader = pypdf.PdfReader( str( draft ))
    got = { name: field.get( "/V" ) for name, field in ( reader.get_fields() or {}).items()}
    wrong = []
    for name, want in fill[ "values" ].items():
        value = got.get( name )
        if str( value if value is not None else "" ) != str( want ):
            wrong.append( f"{ name }: wanted { want!r}, found { value!r}" )
    checks.append( CheckResult( name="every value reads back as written", passed=not wrong,
                                detail="; ".join( wrong[ :6 ]) or f"{ len( fill[ 'values' ])} fields" ))
    clipped = []
    for page in reader.pages:
        for annot in page.get( "/Annots" ) or []:
            widget = annot.get_object()
            name = widget.get( "/T" )
            if name is None or str( name ) not in fill[ "values" ] or widget.get( "/FT" ) == "/Btn":
                continue
            rect = [ float( x ) for x in widget[ "/Rect" ]]
            width = abs( rect[ 2 ] - rect[ 0 ])
            text = str( fill[ "values" ][ str( name )])
            if len( text ) * 10 * 0.55 > width:       # 10pt Helvetica averages ~0.55em a character
                clipped.append( f"{ name } ({ text })" )
    checks.append( CheckResult( name="no value is too wide for its box", passed=not clipped,
                                detail=", ".join( clipped ) or "all fit" ))
    original = pypdf.PdfReader( str( blank ))
    checks.append( CheckResult( name="all pages present", passed=len( reader.pages ) == len( original.pages ),
                                detail=f"{ len( reader.pages )} of { len( original.pages )}" ))
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run([ "pdftoppm", "-r", "40", "-png", str( draft ), f"{ scratch }/p" ], capture_output=True, timeout=120 )
        rendered = len( list( Path( scratch ).glob( "p*.png" )))
    checks.append( CheckResult( name="every page renders", passed=rendered == len( reader.pages ),
                                detail=f"{ rendered } page images" ))
    same = hashlib.sha256( blank.read_bytes()).hexdigest() == fill[ "blank_sha256" ]
    checks.append( CheckResult( name="official blank form unchanged", passed=same, detail=blank.name ))
    kit.save( "pdf_checks", checks )
    failed = [ c for c in checks if not c.passed ]
    handle.log( checks=len( checks ), failed=[ c.name for c in failed ])
    kit.line = ( f"The draft PDF passes all { len( checks )} checks." if not failed else
                 "The draft PDF has problems: " + "; ".join( f"{ c.name } -- { c.detail }" for c in failed ) + "." )


def phases( kit: Kit ):
    return [( "validate", "Read the draft back and check it", _validate )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Liz: Read Tax PDF -- one year's official resident packet, page by page.

Text layer first; a page whose text is missing or decodes to symbols is
OCR'd (pdftoppm + tesseract). The result is cached so nothing is read twice.

    reads:  forms_downloaded
    writes: pages_<year>   ({ source, pages[], ocr_pages[], unreadable[] })

    lancedb_memory/.venv/bin/python adws/adw_liz_read_tax_pdf.py --year 2025 [--force]
"""
from __future__ import annotations

import sys
from pathlib import Path

from liz_adw import Kit, LizStep, run_step
from city_tax.models import FormKind, OfficialForm
from city_tax.state import WorkflowState

STEP = LizStep( "read_tax_pdf", WorkflowState.COMPARING_FORMS, "Read an official packet's text",
                reads=( "forms_downloaded", ), writes=( "pages_<year>", ), suffix_arg="year" )


def packet( forms: list[ OfficialForm ], year: int ) -> OfficialForm:
    for form in forms:
        if form.tax_year == year and form.residency == "resident" and form.kind is FormKind.INSTRUCTIONS:
            return form
    raise RuntimeError( f"No { year } resident instructions packet has been downloaded." )


def _read( kit: Kit, handle ) -> None:
    form = packet([ OfficialForm.model_validate( f ) for f in kit.need( "forms_downloaded" )], kit.args.year )
    reader = kit.pdf_reader()
    pages = reader.pages( Path( form.local_path ))
    kit.save( f"pages_{ kit.args.year }", { "source": form.url, "archived": form.archived, "pages": pages,
                                            "ocr_pages": reader.ocr_pages, "unreadable": reader.unreadable })
    handle.log( pages=len( pages ), ocr=reader.ocr_pages, unreadable=reader.unreadable )
    kit.line = ( f"Read the { kit.args.year } packet: { len( pages )} pages, { len( reader.ocr_pages )} by OCR"
                 + ( f", { len( reader.unreadable )} unreadable" if reader.unreadable else "" ) + "." )


def add_args( parser ) -> None:
    parser.add_argument( "--year", type=int, required=True )


def phases( kit: Kit ):
    return [( "read_pages", "Extract every page's text, OCR where needed", _read )]


if __name__ == "__main__":
    sys.exit( run_step( STEP, __doc__, phases, add_args ))

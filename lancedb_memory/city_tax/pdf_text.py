"""IPdfTextReader: `pdftotext` per page, OCR where the text layer is unreadable.

The city's packets mix good text layers with pages whose embedded fonts
decode to symbols (2025, page 6) or have no text at all (2024's form pages).
A page is OCR'd at 200 dpi when its text is short or mostly characters that
no English form prints. Never raises: a page that can't be read either way is
an empty string, and `unreadable` says which.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

_PLAIN = re.compile( r"[A-Za-z0-9 \n\t.,;:()$%'\"/&#\-–—’“”½•►]" )


def looks_garbled( text: str ) -> bool:
    body = text.strip()
    if len( body ) < 120:
        return True
    plain = len( _PLAIN.findall( body ))
    return plain / len( body ) < 0.9


def _run( argv: list[ str ], timeout: float = 120.0 ) -> str:
    try:
        done = subprocess.run( argv, capture_output=True, text=True, timeout=timeout )
    except ( OSError, subprocess.TimeoutExpired ):
        return ""
    return done.stdout if done.returncode == 0 else ""


def page_count( pdf: Path ) -> int:
    match = re.search( r"^Pages:\s+(\d+)", _run([ "pdfinfo", str( pdf )]), re.M )
    return int( match.group( 1 )) if match else 0


class PopplerTesseractReader:
    def __init__( self, dpi: int = 200 ) -> None:
        self._dpi = dpi
        self.unreadable: list[ int ] = []
        self.ocr_pages: list[ int ] = []

    def _ocr( self, pdf: Path, number: int ) -> str:
        with tempfile.TemporaryDirectory() as scratch:
            stem = Path( scratch ) / "page"
            _run([ "pdftoppm", "-f", str( number ), "-l", str( number ), "-r", str( self._dpi ), "-gray",
                   "-png", str( pdf ), str( stem )])
            images = sorted( Path( scratch ).glob( "page*.png" ))
            return _run([ "tesseract", str( images[ 0 ]), "stdout" ]) if images else ""

    def pages( self, pdf: Path ) -> list[ str ]:
        self.unreadable, self.ocr_pages = [], []
        out: list[ str ] = []
        for number in range( 1, page_count( pdf ) + 1 ):
            text = _run([ "pdftotext", "-f", str( number ), "-l", str( number ), str( pdf ), "-" ])
            if looks_garbled( text ):
                text = self._ocr( pdf, number )
                self.ocr_pages.append( number )
                if not text.strip():
                    self.unreadable.append( number )
            out.append( text )
        return out

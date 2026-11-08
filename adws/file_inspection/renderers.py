"""One renderer per kind of file. Each writes page images into `out_dir` and nothing anywhere else.

Every renderer imports its library inside `render`, so a missing library is a `note` on the result
("no PDF renderer installed") rather than an ImportError for every other file type.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from .models import Inspection, PageImage

#: Sizes by longest side. "full" is what a reader zooms into to read a plat's lot numbers; "preview"
#: is what it looks at first. A page smaller than a size is never enlarged.
RENDITIONS: tuple[ tuple[ str, int ], ... ] = (( "preview", 1280 ), ( "full", 2600 ))
MAX_PAGES  = 20
MAX_TEXT   = 100_000
JPEG_QUALITY = 85
#: A scanned plat at 600 dpi is ~70 megapixels. Pillow refuses past 178M; this keeps that guard,
#: a little looser, so a real survey sheet opens and a decompression bomb still does not.
MAX_PIXELS = 300_000_000

RASTER = frozenset({ "image/tiff", "image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp" })
TEXTUAL = frozenset({ "text/plain", "text/csv", "text/tab-separated-values", "application/json", "application/xml",
                      "text/markdown", "text/html" })
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _clip( text: str ) -> tuple[ str, bool ]:
    return ( text[ :MAX_TEXT ], True ) if len( text ) > MAX_TEXT else ( text, False )


def _digest( data: bytes ) -> str:
    return hashlib.sha256( data ).hexdigest()


def save_page( image, page: int, out_dir: Path ) -> list[ PageImage ]:
    """Both renditions of one Pillow image. Bilevel line art stays lossless PNG; everything else is JPEG."""
    from PIL import Image

    line_art = image.mode == "1"
    base = image.convert( "L" if line_art or image.mode in ( "L", "I;16", "I", "F" ) else "RGB" )
    out: list[ PageImage ] = []
    for rendition, side in RENDITIONS:
        copy = base.copy()
        copy.thumbnail(( side, side ), Image.LANCZOS )
        buffer = io.BytesIO()
        if line_art:
            copy.save( buffer, "PNG", optimize=True )
            mime, suffix = "image/png", "png"
        else:
            copy.save( buffer, "JPEG", quality=JPEG_QUALITY, optimize=True )
            mime, suffix = "image/jpeg", "jpg"
        data = buffer.getvalue()
        target = out_dir / f"page-{ page:03d}-{ rendition }.{ suffix }"
        target.write_bytes( data )
        out.append( PageImage( page=page, rendition=rendition, path=str( target ), mime=mime, width=copy.width,
                               height=copy.height, bytes=len( data ), sha256=_digest( data )))
    return out


class RasterRenderer:
    """TIFF (every page of a multipage one), PNG, JPEG, GIF, BMP, WebP."""

    def handles( self, mime: str ) -> bool:
        return mime in RASTER

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        try:
            from PIL import Image, ImageSequence
        except ImportError:
            return Inspection( kind="image", mime=mime, note="no image library (Pillow) is installed to render this" )
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        pages: list[ PageImage ] = []
        with Image.open( source ) as opened:
            count = getattr( opened, "n_frames", 1 )
            for number, frame in enumerate( ImageSequence.Iterator( opened ), start=1 ):
                if number > MAX_PAGES:
                    break
                pages += save_page( frame.copy(), number, out_dir )
        note = f"{ count } pages; the first { MAX_PAGES } were rendered" if count > MAX_PAGES else ""
        return Inspection( kind="image", mime=mime, page_count=count, pages=pages, note=note )


class PdfRenderer:
    """Each page as an image, plus the PDF's own text layer when it has one."""

    def handles( self, mime: str ) -> bool:
        return mime == "application/pdf"

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        try:
            import fitz                                     # PyMuPDF
            from PIL import Image
        except ImportError:
            return Inspection( kind="pdf", mime=mime, note="no PDF renderer (PyMuPDF) is installed to render this" )
        pages: list[ PageImage ] = []
        texts: list[ str ] = []
        with fitz.open( source ) as document:
            count = document.page_count
            for index, page in enumerate( document ):
                texts.append( page.get_text())
                if index >= MAX_PAGES:
                    continue
                longest = max( page.rect.width, page.rect.height ) or 1
                zoom = RENDITIONS[ -1 ][ 1 ] / longest
                pixmap = page.get_pixmap( matrix=fitz.Matrix( zoom, zoom ), alpha=False )
                image = Image.frombytes( "RGB", ( pixmap.width, pixmap.height ), pixmap.samples )
                pages += save_page( image, index + 1, out_dir )
        joined = "\n\f".join( f"[page { n }]\n{ t.strip() }" for n, t in enumerate( texts, start=1 ) if t.strip())
        text, cut = _clip( joined )
        note = f"{ count } pages; the first { MAX_PAGES } were rendered" if count > MAX_PAGES else ""
        if not text:
            note = ( note + "; " if note else "" ) + "no text layer -- read the page images"
        return Inspection( kind="pdf", mime=mime, page_count=count, pages=pages, text=text, text_truncated=cut, note=note )


class _TextOnly( HTMLParser ):
    SKIP = { "script", "style", "head", "noscript" }

    def __init__( self ) -> None:
        super().__init__()
        self.parts: list[ str ] = []
        self._skipping = 0

    def handle_starttag( self, tag, attrs ):
        if tag in self.SKIP:
            self._skipping += 1
        elif tag in ( "br", "p", "div", "tr", "li", "h1", "h2", "h3" ):
            self.parts.append( "\n" )

    def handle_endtag( self, tag ):
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1

    def handle_data( self, data ):
        if not self._skipping:
            self.parts.append( data )


class TextRenderer:
    """Plain text, CSV, JSON, XML, Markdown, and HTML read as its visible words."""

    def handles( self, mime: str ) -> bool:
        return mime in TEXTUAL

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        with source.open( "rb" ) as handle:
            raw = handle.read( MAX_TEXT * 4 + 1 )
        text = raw.decode( "utf-8", errors="replace" )
        if mime == "text/html":
            parser = _TextOnly()
            parser.feed( text )
            text = re.sub( r"\n\s*\n+", "\n\n", "".join( parser.parts )).strip()
        clipped, cut = _clip( text )
        return Inspection( kind="text", mime=mime, page_count=1, text=clipped, text_truncated=cut or len( raw ) > MAX_TEXT * 4 )


class DocxRenderer:
    """A Word document's paragraphs, read straight out of its XML. Nothing is executed."""

    def handles( self, mime: str ) -> bool:
        return mime == DOCX

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        with zipfile.ZipFile( source ) as archive:
            xml = archive.read( "word/document.xml" ).decode( "utf-8", errors="replace" )
        xml = re.sub( r"</w:p>", "\n", xml )
        xml = re.sub( r"<w:tab/>", "\t", xml )
        text = html.unescape( re.sub( r"<[^>]+>", "", xml )).strip()
        clipped, cut = _clip( text )
        return Inspection( kind="document", mime=mime, page_count=1, text=clipped, text_truncated=cut )


class XlsxRenderer:
    """Each sheet's cell values as CSV text, values only (formulas are shown as their last computed value)."""

    def handles( self, mime: str ) -> bool:
        return mime == XLSX

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        try:
            import openpyxl
        except ImportError:
            return Inspection( kind="spreadsheet", mime=mime, note="no spreadsheet reader (openpyxl) is installed" )
        book = openpyxl.load_workbook( source, read_only=True, data_only=True )
        out = io.StringIO()
        try:
            for sheet in book.worksheets:
                out.write( f"[sheet { sheet.title }]\n" )
                writer = csv.writer( out )
                for row in sheet.iter_rows( values_only=True ):
                    writer.writerow([ "" if v is None else v for v in row ])
                    if out.tell() > MAX_TEXT:
                        break
            count = len( book.worksheets )
        finally:
            book.close()
        clipped, cut = _clip( out.getvalue())
        return Inspection( kind="spreadsheet", mime=mime, page_count=count, text=clipped, text_truncated=cut )


class ZipRenderer:
    """An archive's table of contents. It is never unpacked."""

    def handles( self, mime: str ) -> bool:
        return mime == "application/zip"

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        with zipfile.ZipFile( source ) as archive:
            entries = archive.infolist()
        lines = [ f"{ e.file_size:>12,}  { e.filename }" for e in entries ]
        clipped, cut = _clip( f"{ len( entries ) } entries (not unpacked):\n" + "\n".join( lines ))
        return Inspection( kind="archive", mime=mime, page_count=len( entries ), text=clipped, text_truncated=cut )

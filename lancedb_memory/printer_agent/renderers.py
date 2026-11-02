"""IDocumentRenderer implementations, and the registry that picks one by kind.

Every renderer turns a document into 300 dpi greyscale PNG pages sized for US
Letter, so the backend only ever prints page images and never interprets a
document format itself. `TextRenderer` lays the text out with `text_layout`
and draws it in DejaVu Sans Mono (Pillow, imported only when it draws);
`PdfRenderer` asks poppler's `pdfinfo` / `pdftoppm`, run with an argv list and
a timeout, for exactly the pages the range selects. A new format (DOCX,
images) is one more class and one `register()` call.
"""
from __future__ import annotations

import io
import re
import shutil
import tempfile
from pathlib import Path

from .errors import RequestRefused
from .interfaces import ICommandRunner, IDocumentRenderer
from .models import PrintDocument, PrintOptions, RenderedDocument, RenderedPage
from .text_layout import cells, paginate, select_pages

DPI       = 300
LETTER    = ( 8.5, 11.0 )
MARGIN_IN = 1.0
FONT_PT   = 10.0
LINE_GAP  = 1.25
MONO_FONT = Path( "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf" )
WIDE_FONT = Path( "/mnt/c/Windows/Fonts/ARIALUNI.TTF" )     # CJK and other wide glyphs, when present
PAGES_RE  = re.compile( rb"^Pages:\s+(\d+)\s*$", re.M )


class RenderError( RequestRefused ):
    """A document that cannot be turned into pages. The message is safe to show; it holds no content."""


class TextGrid:
    """How many cells and rows fit on a page, for the font and orientation. Pure arithmetic."""

    def __init__( self, orientation: str, font_pt: float = FONT_PT ) -> None:
        width, height = LETTER if orientation == "portrait" else ( LETTER[ 1 ], LETTER[ 0 ])
        self.size       = ( int( width * DPI ), int( height * DPI ))
        self.margin     = int( MARGIN_IN * DPI )
        self.font_px    = int( round( font_pt / 72 * DPI ))
        self.cell_px    = int( round( self.font_px * 0.602 ))         # DejaVu Sans Mono advance
        self.line_px    = int( round( self.font_px * LINE_GAP ))
        self.cols       = ( self.size[ 0 ] - 2 * self.margin ) // self.cell_px
        self.rows       = ( self.size[ 1 ] - 2 * self.margin ) // self.line_px - 3   # header, gap, footer


class TextRenderer:
    """Plain text: a title line, the body, `Page n of m` at the foot of every page."""

    def __init__( self, max_pages: int, mono_font: Path = MONO_FONT, wide_font: Path = WIDE_FONT ) -> None:
        self._max_pages = max_pages
        self._mono      = mono_font
        self._wide      = wide_font

    def layout( self, document: PrintDocument, options: PrintOptions ) -> tuple[ TextGrid, list[ list[ str ]], list[ int ]]:
        """The pages and which of them to print -- everything but the drawing, so tests need no Pillow."""
        try:
            text = document.data.decode( "utf-8" )
        except UnicodeDecodeError:
            raise RenderError( "the text is not valid UTF-8" ) from None
        grid  = TextGrid( options.orientation )
        pages = paginate( text, grid.cols, grid.rows )
        chosen = select_pages( len( pages ), options.page_range )
        if len( chosen ) > self._max_pages:
            raise RenderError( f"{ len( chosen )} pages is more than the { self._max_pages }-page limit" )
        return grid, pages, chosen

    def render( self, document: PrintDocument, options: PrintOptions ) -> RenderedDocument:
        try:
            grid, pages, chosen = self.layout( document, options )
        except ValueError as exc:
            raise RenderError( str( exc )) from None
        from PIL import Image, ImageDraw, ImageFont          # only the drawing needs Pillow

        mono = ImageFont.truetype( str( self._mono ), grid.font_px )
        wide = ImageFont.truetype( str( self._wide ), grid.font_px ) if self._wide.exists() else mono
        out: list[ RenderedPage ] = []
        for n in chosen:
            page = Image.new( "L", grid.size, 255 )
            draw = ImageDraw.Draw( page )
            top  = grid.margin
            self._row( draw, grid, top, document.title[ :grid.cols ], mono, wide )
            for i, row in enumerate( pages[ n - 1 ]):
                self._row( draw, grid, top + ( i + 2 ) * grid.line_px, row, mono, wide )
            footer = f"Page { n } of { len( pages )}"
            x = grid.size[ 0 ] - grid.margin - len( footer ) * grid.cell_px
            draw.text(( x, grid.size[ 1 ] - grid.margin - grid.line_px ), footer, font=mono, fill=0 )
            buffer = io.BytesIO()
            page.save( buffer, format="PNG", dpi=( DPI, DPI ))
            out.append( RenderedPage( png=buffer.getvalue()))
        return RenderedDocument( pages=out, dpi=DPI )

    @staticmethod
    def _row( draw: object, grid: TextGrid, y: int, row: str, mono: object, wide: object ) -> None:
        x = grid.margin
        for ch in row:
            width = cells( ch )
            if ch != " ":
                draw.text(( x, y ), ch, font=wide if width == 2 else mono, fill=0 )   # type: ignore[attr-defined]
            x += width * grid.cell_px


class PdfRenderer:
    """PDF pages through poppler. The bytes go to a private temp file, never to a caller-chosen path."""

    def __init__( self, runner: ICommandRunner, max_pages: int, work_root: Path, timeout: float = 120.0 ) -> None:
        self._runner    = runner
        self._max_pages = max_pages
        self._root      = work_root
        self._timeout   = timeout

    def page_count( self, pdf: Path ) -> int:
        result = self._runner.run([ "pdfinfo", str( pdf )], timeout=30.0 )
        found  = PAGES_RE.search( result.stdout )
        if result.returncode != 0 or found is None:
            raise RenderError( "the PDF could not be read (pdfinfo refused it)" )
        return int( found.group( 1 ))

    def render( self, document: PrintDocument, options: PrintOptions ) -> RenderedDocument:
        if not document.data.startswith( b"%PDF-" ):
            raise RenderError( "the document is not a PDF" )
        self._root.mkdir( mode=0o700, parents=True, exist_ok=True )
        work = Path( tempfile.mkdtemp( prefix="render_", dir=self._root ))
        try:
            pdf = work / "in.pdf"
            pdf.write_bytes( document.data )
            count = self.page_count( pdf )
            try:
                chosen = select_pages( count, options.page_range )
            except ValueError as exc:
                raise RenderError( str( exc )) from None
            if len( chosen ) > self._max_pages:
                raise RenderError( f"{ len( chosen )} pages is more than the { self._max_pages }-page limit" )
            out: list[ RenderedPage ] = []
            for n in chosen:
                stem = work / f"p{ n }"
                result = self._runner.run([ "pdftoppm", "-r", str( DPI ), "-gray", "-png", "-singlefile",
                                            "-f", str( n ), "-l", str( n ), str( pdf ), str( stem )], timeout=self._timeout )
                image = stem.with_suffix( ".png" )
                if result.returncode != 0 or not image.exists():
                    raise RenderError( f"page { n } of the PDF could not be rendered" )
                out.append( RenderedPage( png=image.read_bytes()))
            return RenderedDocument( pages=out, dpi=DPI )
        finally:
            shutil.rmtree( work, ignore_errors=True )


class RendererRegistry:
    """Strategy by document kind. Unknown kinds are refused, not guessed."""

    def __init__( self ) -> None:
        self._by_kind: dict[ str, IDocumentRenderer ] = {}

    def register( self, kind: str, renderer: IDocumentRenderer ) -> RendererRegistry:
        self._by_kind[ kind ] = renderer
        return self

    def render( self, document: PrintDocument, options: PrintOptions ) -> RenderedDocument:
        renderer = self._by_kind.get( document.kind )
        if renderer is None:
            raise RenderError( f"{ document.kind } documents cannot be printed yet" )
        return renderer.render( document, options )

"""Text layout, the text renderer (Pillow) and the PDF renderer (poppler mocked)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from fakes import FakeRunner
from printer_agent.models import CommandOutput, PrintDocument, PrintOptions
from printer_agent.renderers import PdfRenderer, RenderError, RendererRegistry, TextGrid, TextRenderer
from printer_agent.text_layout import cells, clean, paginate, select_pages, wrap_line


def doc( data: bytes, kind: str = "text" ) -> PrintDocument:
    return PrintDocument( kind=kind, title="T", data=data, sha256="0" * 64, source="inline" )


def test_wrap_breaks_at_spaces_and_hard_wraps_long_words():
    assert wrap_line( "aaa bbb ccc", 7 ) == [ "aaa bbb", "ccc" ]
    assert wrap_line( "x" * 10, 4 ) == [ "xxxx", "xxxx", "xx" ]
    assert wrap_line( "\tx", 10 ) == [ "    x" ]


def test_wide_characters_take_two_cells():
    assert cells( "漢" ) == 2 and cells( "a" ) == 1
    assert wrap_line( "漢字漢字", 5 ) == [ "漢字", "漢字" ]


def test_control_characters_are_dropped_and_newlines_normalised():
    assert clean( "a\r\nb\rc\x07\u200bd" ) == "a\nb\ncd"


def test_pagination_and_page_ranges():
    pages = paginate( "\n".join( str( n ) for n in range( 25 )), 10, 10 )
    assert len( pages ) == 3 and pages[ 2 ] == [ str( n ) for n in range( 20, 25 )]
    assert select_pages( 5, "1,3-4,3" ) == [ 1, 3, 4 ]
    with pytest.raises( ValueError ):
        select_pages( 2, "1-3" )


def test_letter_grid_has_readable_margins():
    grid = TextGrid( "portrait" )
    assert grid.size == ( 2550, 3300 ) and grid.margin == 300
    assert 70 <= grid.cols <= 85 and 45 <= grid.rows <= 60
    assert TextGrid( "landscape" ).cols > grid.cols


def test_text_layout_respects_page_limit_and_range():
    renderer = TextRenderer( max_pages=2 )
    long = doc(( "line\n" * 500 ).encode())
    with pytest.raises( RenderError ):
        renderer.layout( long, PrintOptions())
    _, pages, chosen = renderer.layout( long, PrintOptions( page_range="2-3" ))
    assert chosen == [ 2, 3 ] and len( pages ) > 3
    with pytest.raises( RenderError ):
        renderer.layout( doc( b"\xff\xfe" ), PrintOptions())


def test_text_renders_letter_pngs():
    pytest.importorskip( "PIL" )
    from PIL import Image
    font = Path( "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf" )
    if not font.exists():
        pytest.skip( "DejaVu Sans Mono is not installed" )
    out = TextRenderer( max_pages=5 ).render( doc( "Héllo — wörld\n漢字".encode()), PrintOptions())
    assert len( out.pages ) == 1
    image = Image.open( io.BytesIO( out.pages[ 0 ].png ))
    assert image.size == ( 2550, 3300 ) and image.mode == "L"
    assert image.getextrema()[ 0 ] < 128          # something dark was drawn


def test_pdf_renderer_runs_poppler_per_selected_page( tmp_path ):
    class Poppler( FakeRunner ):
        def run( self, argv, timeout, stdin=None ):
            self.calls.append(( argv, stdin ))
            if argv[ 0 ] == "pdfinfo":
                return CommandOutput( returncode=0, stdout=b"Title: x\nPages:          4\n" )
            Path( argv[ -1 ] + ".png" ).write_bytes( b"\x89PNG page" )
            return CommandOutput( returncode=0 )

    runner = Poppler()
    out = PdfRenderer( runner, max_pages=10, work_root=tmp_path / "work" ).render(
        doc( b"%PDF-1.7 ...", "pdf" ), PrintOptions( page_range="2,4" ))
    assert len( out.pages ) == 2
    firsts = [ argv[ argv.index( "-f" ) + 1 ] for argv, _ in runner.calls if argv[ 0 ] == "pdftoppm" ]
    assert firsts == [ "2", "4" ]
    assert all( "-gray" in argv for argv, _ in runner.calls if argv[ 0 ] == "pdftoppm" )
    assert list(( tmp_path / "work" ).iterdir()) == []        # temp files removed


def test_pdf_renderer_refuses_non_pdf_and_bad_pdf( tmp_path ):
    with pytest.raises( RenderError ):
        PdfRenderer( FakeRunner(), 10, tmp_path ).render( doc( b"not a pdf", "pdf" ), PrintOptions())
    bad = FakeRunner({ "pdfinfo": CommandOutput( returncode=1 )})
    with pytest.raises( RenderError ):
        PdfRenderer( bad, 10, tmp_path ).render( doc( b"%PDF-1.4", "pdf" ), PrintOptions())


def test_registry_refuses_unknown_kinds():
    with pytest.raises( RenderError ):
        RendererRegistry().render( doc( b"x" ), PrintOptions())

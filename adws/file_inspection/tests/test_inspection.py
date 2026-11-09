"""file_inspection: sniffing, mismatch, and each renderer on a small real file."""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from file_inspection.inspector import default_inspector  # noqa: E402
from file_inspection.sniff import mismatch, sniff_mime  # noqa: E402

SHA = "ab" * 32


def multipage_tiff( path: Path, pages: int = 3, mode: str = "1" ) -> Path:
    from PIL import Image
    frames = [ Image.new( mode, ( 400 + 10 * n, 300 ), 1 if mode == "1" else "white" ) for n in range( pages )]
    frames[ 0 ].save( path, save_all=True, append_images=frames[ 1: ], compression="tiff_lzw" if mode != "1" else "group4" )
    return path


def test_tiff_every_page_is_rendered_in_two_sizes_and_the_original_is_untouched( tmp_path: Path ) -> None:
    pytest.importorskip( "PIL" )
    source = multipage_tiff( tmp_path / "0337201.tif" )
    before = source.read_bytes()
    result = default_inspector( tmp_path / "out" ).inspect( source, SHA )
    assert result.mime == "image/tiff" and result.kind == "image"
    assert result.page_count == 3
    assert sorted({( p.page, p.rendition ) for p in result.pages }) == [
        ( 1, "full" ), ( 1, "preview" ), ( 2, "full" ), ( 2, "preview" ), ( 3, "full" ), ( 3, "preview" )]
    assert all( p.mime == "image/png" and Path( p.path ).is_file() for p in result.pages )   # line art stays lossless
    assert all( Path( p.path ).parent == tmp_path / "out" / SHA[ :32 ] for p in result.pages )
    assert source.read_bytes() == before


def test_a_colour_image_becomes_jpeg_and_a_big_one_is_shrunk( tmp_path: Path ) -> None:
    from PIL import Image
    source = tmp_path / "photo.png"
    Image.new( "RGB", ( 4000, 1000 ), "red" ).save( source )
    result = default_inspector( tmp_path / "out" ).inspect( source, SHA )
    preview = next( p for p in result.pages if p.rendition == "preview" )
    full = next( p for p in result.pages if p.rendition == "full" )
    assert preview.mime == "image/jpeg" and preview.width == 1280
    assert full.width == 2600


def test_pdf_pages_and_text( tmp_path: Path ) -> None:
    fitz = pytest.importorskip( "fitz" )
    source = tmp_path / "doc.pdf"
    document = fitz.open()
    for words in ( "first page", "second page" ):
        document.new_page().insert_text(( 72, 72 ), words )
    document.save( source )
    result = default_inspector( tmp_path / "out" ).inspect( source, SHA )
    assert result.kind == "pdf" and result.page_count == 2
    assert "first page" in result.text and "second page" in result.text
    assert len( result.pages ) == 4


def test_csv_and_html_are_read_as_text( tmp_path: Path ) -> None:
    csv_file = tmp_path / "a.csv"
    csv_file.write_text( "a,b\n1,2\n" )
    page = tmp_path / "a.html"
    page.write_text( "<html><head><script>x()</script></head><body><p>Hello</p><p>World</p></body></html>" )
    inspector = default_inspector( tmp_path / "out" )
    assert inspector.inspect( csv_file, SHA ).text == "a,b\n1,2\n"
    text = inspector.inspect( page, SHA ).text
    assert "Hello" in text and "World" in text and "x()" not in text


def test_docx_xlsx_and_zip( tmp_path: Path ) -> None:
    docx = tmp_path / "a.docx"
    with zipfile.ZipFile( docx, "w" ) as archive:
        archive.writestr( "[Content_Types].xml", "<Types/>" )
        archive.writestr( "word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Plat &amp; survey</w:t></w:r></w:p>"
                                                "<w:p><w:r><w:t>Lot 12</w:t></w:r></w:p></w:body></w:document>" )
    openpyxl = pytest.importorskip( "openpyxl" )
    xlsx = tmp_path / "a.xlsx"
    book = openpyxl.Workbook()
    book.active.append([ "lot", "acres" ])
    book.active.append([ 12, 1.5 ])
    book.save( xlsx )
    plain = tmp_path / "a.zip"
    with zipfile.ZipFile( plain, "w" ) as archive:
        archive.writestr( "../evil.txt", "x" )
        archive.writestr( "inner/readme.txt", "hello" )
    inspector = default_inspector( tmp_path / "out" )
    assert inspector.inspect( docx, SHA ).text == "Plat & survey\nLot 12"
    sheet = inspector.inspect( xlsx, SHA )
    assert sheet.kind == "spreadsheet" and "lot,acres" in sheet.text and "12,1.5" in sheet.text
    listing = inspector.inspect( plain, SHA )
    assert listing.kind == "archive" and listing.page_count == 2 and "inner/readme.txt" in listing.text
    assert not ( tmp_path / "evil.txt" ).exists() and not ( tmp_path / "out" / "evil.txt" ).exists()


def test_a_tif_that_is_really_a_sign_in_page_is_named_as_such( tmp_path: Path ) -> None:
    fake = tmp_path / "0337201.tif"
    fake.write_text( "<!DOCTYPE html><html><body>Please sign in</body></html>" )
    mime = sniff_mime( fake )
    assert mime == "text/html"
    assert "sign-in page" in mismatch( fake, mime )
    real_html = tmp_path / "page.html"
    real_html.write_text( "<html></html>" )
    assert mismatch( real_html, sniff_mime( real_html )) == ""


def test_a_damaged_file_is_a_note_not_a_crash( tmp_path: Path ) -> None:
    broken = tmp_path / "broken.tif"
    broken.write_bytes( b"II*\x00" + b"\xff" * 64 )
    result = default_inspector( tmp_path / "out" ).inspect( broken, SHA )
    assert result.kind == "binary" and "could not be opened" in result.note


def test_unknown_binary_gets_a_note( tmp_path: Path ) -> None:
    blob = tmp_path / "thing.bin"
    blob.write_bytes( bytes( range( 256 )))
    result = default_inspector( tmp_path / "out" ).inspect( blob, SHA )
    assert result.kind == "binary" and result.pages == []


# -- Word 97 and Excel 97: the old binary formats, read as text, never run ---------------------
FIXTURES = Path( __file__ ).resolve().parent / "fixtures"


def test_an_old_word_document_is_sniffed_from_its_streams_and_its_text_read( tmp_path: Path ) -> None:
    pytest.importorskip( "olefile" )
    source = tmp_path / "download.bin"                      # no telling extension: the streams decide
    source.write_bytes(( FIXTURES / "deed.doc" ).read_bytes())
    assert sniff_mime( source ) == "application/msword"
    result = default_inspector( tmp_path / "out" ).inspect( source, SHA )
    assert result.kind == "document" and result.page_count == 1
    assert result.text == "Fralick's Addition, Liber 4 of Plats, Page 31.\nLot 12 and Lot 13."


def test_unicode_pieces_and_a_table_come_out_as_lines_and_tabs( tmp_path: Path ) -> None:
    pytest.importorskip( "olefile" )
    result = default_inspector( tmp_path / "out" ).inspect( FIXTURES / "table.doc", SHA )
    assert result.text == "Deed of Distribution — Łódź test\nGrantee\tShare\nEG\t½\nEnd."


def test_an_old_spreadsheet_is_every_sheet_as_csv( tmp_path: Path ) -> None:
    pytest.importorskip( "olefile" )
    pytest.importorskip( "xlrd" )
    source = FIXTURES / "plats.xls"
    assert sniff_mime( source ) == "application/vnd.ms-excel"
    result = default_inspector( tmp_path / "out" ).inspect( source, SHA )
    assert result.kind == "spreadsheet" and result.page_count == 1
    assert result.text.splitlines()[ :3 ] == [ "[sheet plats]", "Parcel,Liber,Page", "41-13-25-101-012,4,31" ]


def test_an_encrypted_or_damaged_word_document_is_a_note_never_a_guess() -> None:
    from file_inspection.legacy import LegacyDocError, doc_text
    word = bytearray( 0x400 )
    word[ 0:2 ] = b"\xec\xa5"
    word[ 0x0A:0x0C ] = ( 0x0100 ).to_bytes( 2, "little" )
    with pytest.raises( LegacyDocError, match="encrypted" ):
        doc_text( bytes( word ), { "0Table": b"" })
    word[ 0x0A:0x0C ] = b"\x00\x00"
    with pytest.raises( LegacyDocError ):
        doc_text( bytes( word ), { "0Table": b"" })
    with pytest.raises( LegacyDocError, match="no FIB" ):
        doc_text( b"\x00" * 0x400, {})


def test_an_ole_file_that_is_neither_is_not_called_word( tmp_path: Path ) -> None:
    olefile = pytest.importorskip( "olefile" )
    from file_inspection.legacy import ole_kind
    junk = tmp_path / "x.doc"
    junk.write_bytes( b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600 )
    assert ole_kind( junk ) == "application/x-ole-storage"
    assert olefile.isOleFile( str( FIXTURES / "deed.doc" ))

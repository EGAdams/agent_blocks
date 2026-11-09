"""Word 97-2003 (.doc) and Excel 97-2003 (.xls): the text of the old binary Office formats, read, never run.

Both are OLE2 compound files (first bytes `D0 CF 11 E0 A1 B1 1A E1`), so the first bytes alone cannot say
which one a file is; `ole_kind` looks at the streams inside (`WordDocument`, `Workbook`/`Book`). A county
recorder or probate court still serves forms this way, and until this module they came back as `binary`
with "no viewer".

Nothing here executes anything. A macro is a stream like any other and is never read; the text comes out of
the document's own character data:

- **.doc** -- `WordDocument` holds the characters and the FIB; the FIB names the table stream (`0Table` or
  `1Table`) and where in it the piece table (the Clx) is. Each piece is a run of characters, stored either as
  cp1252 bytes ("compressed") or UTF-16LE. That is [MS-DOC] 2.4.1 "Retrieving Text", and it is the whole
  reader: no formatting, no images. Field codes (`{ HYPERLINK ... }`) are dropped and their shown result kept;
  table cell marks become tabs. An encrypted document is refused with a note, never guessed at.
- **.xls** -- `xlrd` (which reads only the old format since 2.0), cell values only, each sheet as CSV like
  the .xlsx reader, dates as ISO dates.

Each library is imported inside `render`, so a missing one is a note on the result, not an ImportError.
"""
from __future__ import annotations

import csv
import io
import re
import struct
from pathlib import Path

from .models import Inspection

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DOC = "application/msword"
XLS = "application/vnd.ms-excel"
OLE = "application/x-ole-storage"
MAX_TEXT = 100_000
#: A sane ceiling on pieces: a real document has a handful to a few thousand; a hostile one claims millions.
MAX_PIECES = 100_000


def ole_kind( path: Path ) -> str:
    """The Office type of a compound file, from the streams it holds; OLE when it is neither (or unreadable)."""
    try:
        import olefile
    except ImportError:
        return { ".doc": DOC, ".xls": XLS }.get( path.suffix.lower(), OLE )
    try:
        with olefile.OleFileIO( str( path )) as ole:
            if ole.exists( "WordDocument" ):
                return DOC
            if ole.exists( "Workbook" ) or ole.exists( "Book" ):
                return XLS
    except Exception:                                       # a damaged container is just an unknown one
        pass
    return OLE


class LegacyDocError( ValueError ):
    """The document cannot be read as text, said plainly (encrypted, not Word 97+, a damaged piece table)."""


def doc_text( word: bytes, table_streams: dict[ str, bytes ]) -> str:
    """The characters of a Word 97-2003 document, from its WordDocument stream and its table streams."""
    if len( word ) < 0x200 or struct.unpack_from( "<H", word, 0 )[ 0 ] != 0xA5EC:
        raise LegacyDocError( "not a Word 97-2003 document (no FIB)" )
    flags = struct.unpack_from( "<H", word, 0x0A )[ 0 ]
    if flags & 0x0100:
        raise LegacyDocError( "the document is password-protected (encrypted), so its text cannot be read" )
    table_name = "1Table" if flags & 0x0200 else "0Table"
    table = table_streams.get( table_name )
    if table is None:
        raise LegacyDocError( f"the document's { table_name } stream is missing" )
    fc_clx, lcb_clx = _clx_location( word )
    clx = table[ fc_clx:fc_clx + lcb_clx ]
    if lcb_clx == 0 or len( clx ) != lcb_clx:
        raise LegacyDocError( "the document's piece table is outside its table stream" )
    return _clean( "".join( _pieces( word, _plc_pcd( clx ))))


def _clx_location( word: bytes ) -> tuple[ int, int ]:
    """fcClx/lcbClx: pair 33 of FibRgFcLcb97, which starts after FibBase, fibRgW and fibRgLw."""
    at = 32
    csw = struct.unpack_from( "<H", word, at )[ 0 ]
    at += 2 + csw * 2
    cslw = struct.unpack_from( "<H", word, at )[ 0 ]
    at += 2 + cslw * 4
    pairs = struct.unpack_from( "<H", word, at )[ 0 ]
    at += 2
    if pairs <= 33 or at + 34 * 8 > len( word ):
        raise LegacyDocError( "the document's FIB is too short to hold a piece table (pre-Word 97?)" )
    return struct.unpack_from( "<II", word, at + 33 * 8 )


def _plc_pcd( clx: bytes ) -> bytes:
    """Skip the Prc entries (formatting) and return the PlcPcd that follows the Pcdt marker."""
    at = 0
    while at < len( clx ):
        marker = clx[ at ]
        if marker == 0x01:
            at += 3 + struct.unpack_from( "<h", clx, at + 1 )[ 0 ]
        elif marker == 0x02:
            size = struct.unpack_from( "<I", clx, at + 1 )[ 0 ]
            body = clx[ at + 5:at + 5 + size ]
            if len( body ) != size:
                raise LegacyDocError( "the document's piece table is cut short" )
            return body
        else:
            break
    raise LegacyDocError( "the document has no piece table" )


def _pieces( word: bytes, plc: bytes ):
    count = ( len( plc ) - 4 ) // 12
    if count <= 0 or count > MAX_PIECES or 4 * ( count + 1 ) + 8 * count != len( plc ):
        raise LegacyDocError( "the document's piece table is malformed" )
    cps = struct.unpack_from( f"<{ count + 1 }I", plc, 0 )
    total = 0
    for i in range( count ):
        length = cps[ i + 1 ] - cps[ i ]
        if length < 0 or length > len( word ):
            raise LegacyDocError( "the document's piece table is malformed" )
        fc = struct.unpack_from( "<I", plc, 4 * ( count + 1 ) + 8 * i + 2 )[ 0 ]
        if fc & 0x40000000:                                 # compressed: one cp1252 byte per character
            start = ( fc & 0x3FFFFFFF ) // 2
            yield word[ start:start + length ].decode( "cp1252", errors="replace" )
        else:
            yield word[ fc:fc + 2 * length ].decode( "utf-16-le", errors="replace" )
        total += length
        if total > MAX_TEXT * 2:
            return


def _clean( raw: str ) -> str:
    """Field codes out (their shown result stays), cell marks to tabs, paragraph marks to new lines."""
    out, depth_code = [], []
    for ch in raw:
        if ch == "\x13":                                    # field begins: its code follows
            depth_code.append( True )
        elif ch == "\x14":                                  # separator: the shown result follows
            if depth_code:
                depth_code[ -1 ] = False
        elif ch == "\x15":
            if depth_code:
                depth_code.pop()
        elif depth_code and depth_code[ -1 ]:
            continue
        else:
            out.append( ch )
    text = "".join( out ).replace( "\x07\x07", "\n" )   # last cell's mark, then the row's own mark
    text = text.translate({ 0x0D: "\n", 0x0B: "\n", 0x0C: "\n", 0x07: "\t", 0x1E: "-", 0x1F: "",
                                      0xA0: " " })
    text = re.sub( r"[\x00-\x08\x0e-\x1d]", "", text )
    text = re.sub( r"\t\n", "\n", text )                    # the row-end mark after the last cell
    return re.sub( r"\n{3,}", "\n\n", text ).strip()


def _clip( text: str ) -> tuple[ str, bool ]:
    return ( text[ :MAX_TEXT ], True ) if len( text ) > MAX_TEXT else ( text, False )


class LegacyWordRenderer:
    """Word 97-2003 text, out of the piece table. Macros are never read, let alone run."""

    def handles( self, mime: str ) -> bool:
        return mime == DOC

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        try:
            import olefile
        except ImportError:
            return Inspection( kind="document", mime=mime, note="no OLE reader (olefile) is installed to read this" )
        with olefile.OleFileIO( str( source )) as ole:
            word = ole.openstream( "WordDocument" ).read()
            tables = { name: ole.openstream( name ).read() for name in ( "0Table", "1Table" ) if ole.exists( name )}
            macros = ole.exists( "Macros" )
        try:
            text = doc_text( word, tables )
        except LegacyDocError as exc:
            return Inspection( kind="document", mime=mime, note=str( exc ))
        clipped, cut = _clip( text )
        note = "" if text else "the document has no text"
        if macros:
            note = ( note + "; " if note else "" ) + "it carries macros, which were not read or run"
        return Inspection( kind="document", mime=mime, page_count=1, text=clipped, text_truncated=cut, note=note )


class LegacyExcelRenderer:
    """Excel 97-2003 cell values, each sheet as CSV text like the .xlsx reader."""

    def handles( self, mime: str ) -> bool:
        return mime == XLS

    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection:
        try:
            import xlrd
        except ImportError:
            return Inspection( kind="spreadsheet", mime=mime, note="no .xls reader (xlrd) is installed" )
        book = xlrd.open_workbook( str( source ), on_demand=True )
        out = io.StringIO()
        try:
            count = book.nsheets
            for index in range( count ):
                sheet = book.sheet_by_index( index )
                out.write( f"[sheet { sheet.name }]\n" )
                writer = csv.writer( out )
                for row in range( sheet.nrows ):
                    writer.writerow([ _cell( book, c ) for c in sheet.row( row )])
                    if out.tell() > MAX_TEXT:
                        break
                book.unload_sheet( index )
                if out.tell() > MAX_TEXT:
                    break
        finally:
            book.release_resources()
        clipped, cut = _clip( out.getvalue())
        return Inspection( kind="spreadsheet", mime=mime, page_count=count, text=clipped, text_truncated=cut )


def _cell( book, cell ) -> str:
    import xlrd
    if cell.ctype == xlrd.XL_CELL_EMPTY or cell.ctype == xlrd.XL_CELL_BLANK:
        return ""
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            moment = xlrd.xldate.xldate_as_datetime( cell.value, book.datemode )
            return moment.date().isoformat() if moment.time().isoformat() == "00:00:00" else moment.isoformat()
        except Exception:
            return str( cell.value )
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return str( int( cell.value )) if float( cell.value ).is_integer() else repr( cell.value )
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get( cell.value, "#ERR" )
    return str( cell.value )

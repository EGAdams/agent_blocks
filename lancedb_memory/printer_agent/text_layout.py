"""Plain text laid out on a fixed character grid. Pure: no fonts, no images.

The grid is what `TextRenderer` draws in a monospaced face, so a line's width
is its cell count: one cell per character, two for East Asian wide ones.
Tabs expand to four, CR/LF and CR become LF, and every other control
character is dropped rather than drawn as a box.
"""
from __future__ import annotations

import unicodedata

TAB = 4


def cells( ch: str ) -> int:
    return 2 if unicodedata.east_asian_width( ch ) in ( "W", "F" ) else 1


def clean( text: str ) -> str:
    text = text.replace( "\r\n", "\n" ).replace( "\r", "\n" )
    out: list[ str ] = []
    for ch in text:
        if ch == "\n" or ch == "\t":
            out.append( ch )
        elif unicodedata.category( ch ) in ( "Cc", "Cf", "Cs", "Co", "Cn" ):
            continue
        else:
            out.append( ch )
    return "".join( out )


def _expand( line: str ) -> str:
    out: list[ str ] = []
    col = 0
    for ch in line:
        if ch == "\t":
            pad = TAB - col % TAB
            out.append( " " * pad )
            col += pad
        else:
            out.append( ch )
            col += cells( ch )
    return "".join( out )


def wrap_line( line: str, cols: int ) -> list[ str ]:
    """One logical line as rows of at most `cols` cells, breaking at the last space when there is one."""
    line = _expand( line ).rstrip()
    if not line:
        return [ "" ]
    rows: list[ str ] = []
    while line:
        width, cut = 0, 0
        for i, ch in enumerate( line ):
            if width + cells( ch ) > cols:
                break
            width += cells( ch )
            cut = i + 1
        if cut >= len( line ):
            rows.append( line )
            break
        space = line.rfind( " ", 0, cut + 1 )
        if space > 0:
            rows.append( line[ :space ].rstrip())
            line = line[ space + 1: ].lstrip( " " )
        else:
            rows.append( line[ :cut ])
            line = line[ cut: ]
    return rows


def paginate( text: str, cols: int, rows_per_page: int ) -> list[ list[ str ]]:
    rows: list[ str ] = []
    for line in clean( text ).split( "\n" ):
        rows.extend( wrap_line( line, cols ))
    while rows and rows[ -1 ] == "":
        rows.pop()
    if not rows:
        rows = [ "" ]
    return [ rows[ i:i + rows_per_page ] for i in range( 0, len( rows ), rows_per_page )]


def select_pages( count: int, page_range: str | None ) -> list[ int ]:
    """1-based page numbers the range names, in order, without repeats. Out-of-range pages are an error."""
    if page_range is None:
        return list( range( 1, count + 1 ))
    chosen: list[ int ] = []
    for part in page_range.split( "," ):
        first, _, last = part.partition( "-" )
        lo, hi = int( first ), int( last or first )
        if lo < 1 or hi < lo or hi > count:
            raise ValueError( f"page range { page_range } does not fit a { count }-page document" )
        for n in range( lo, hi + 1 ):
            if n not in chosen:
                chosen.append( n )
    return chosen

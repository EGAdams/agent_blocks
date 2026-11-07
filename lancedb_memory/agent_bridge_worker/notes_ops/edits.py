"""INoteEdit Strategies: three ways to locate and change text, one `apply(text) -> text` each.

`edit_from()` is the Factory that turns a validated NoteEdit into the right Strategy. A Strategy raises
NotesRefusal with plain words when it cannot act exactly; nothing is ever guessed.
"""
from __future__ import annotations

import re
from typing import Protocol

from .base import NotesRefusal
from .requests import NoteEdit

_HEADING = re.compile( r"^(#{1,6})\s+(.*?)\s*#*\s*$" )


class INoteEdit(Protocol):
    def apply( self, text: str ) -> str: ...


class FindReplace:
    """Replace the one exact occurrence of `find`."""

    def __init__( self, find: str, replace: str ) -> None:
        self._find, self._replace = find, replace

    def apply( self, text: str ) -> str:
        count = text.count( self._find )
        if count != 1:
            raise NotesRefusal( "its find text " + ( "is not in the note" if count == 0 else f"occurs { count } times" ))
        return text.replace( self._find, self._replace, 1 )


class LineReplace:
    """Replace one whole line (1-based, as the note stands after the edits before it)."""

    def __init__( self, line: int, replace: str ) -> None:
        self._line, self._replace = line, replace

    def apply( self, text: str ) -> str:
        lines = text.split( "\n" )
        count = len( lines ) - ( 1 if text.endswith( "\n" ) else 0 )
        if not 1 <= self._line <= count:
            raise NotesRefusal( f"line { self._line } does not exist (the note has { count } line(s))" )
        lines[ self._line - 1 ] = self._replace
        return "\n".join( lines )


class SectionReplace:
    """Replace everything under one heading, up to the next heading of the same or higher level."""

    def __init__( self, heading: str, replace: str ) -> None:
        self._heading, self._replace = heading.strip(), replace

    def apply( self, text: str ) -> str:
        lines = text.split( "\n" )
        found = [ ( i, len( m.group( 1 ))) for i, line in enumerate( lines )
                  if ( m := _HEADING.match( line )) and m.group( 2 ) == self._heading ]
        if len( found ) != 1:
            raise NotesRefusal( f'the heading "{ self._heading }" ' + ( "is not in the note" if not found
                                                                         else f"occurs { len( found ) } times" ))
        start, level = found[ 0 ]
        end = next(( j for j in range( start + 1, len( lines ))
                     if ( m := _HEADING.match( lines[ j ])) and len( m.group( 1 )) <= level ), len( lines ))
        if end == len( lines ) and lines[ -1 ] == "":     # keep the note's final newline out of the replaced body
            end -= 1
        body = self._replace.strip( "\n" ).split( "\n" ) if self._replace.strip( "\n" ) else []
        tail = lines[ end: ]
        middle = [ "" ] + body + ( [ "" ] if tail and tail != [ "" ] else [] )
        return "\n".join( lines[ :start + 1 ] + middle + tail )


class AppendText:
    """Add text at the end of the note, on its own line(s). A task prompt is capped at 8000 characters, so a long
    note is built as create + several appends."""

    def __init__( self, text: str ) -> None:
        self._text = text

    def apply( self, text: str ) -> str:
        return ( text if text.endswith( "\n" ) or not text else text + "\n" ) + self._text


def edit_from( model: NoteEdit ) -> INoteEdit:
    if model.append is not None:
        return AppendText( model.append )
    if model.find is not None:
        return FindReplace( model.find, model.replace )
    if model.line is not None:
        return LineReplace( model.line, model.replace )
    return SectionReplace( model.section, model.replace )

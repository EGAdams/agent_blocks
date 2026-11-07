"""IHighlightChange Strategies over a note's ``{{highlight:name}}text{{/highlight}}`` spans.

The span grammar is the dashboard's (``notes_formatting``). The palette is checked by the *read-back*: after a
write the dashboard's own ``/highlights`` must report the span, so a colour it does not know can never be
reported as done.
"""
from __future__ import annotations

import re
from typing import Protocol

from .base import NotesRefusal
from .requests import HighlightChange

SPAN = re.compile( r"\{\{highlight:([a-z]+)\}\}(.*?)\{\{/highlight\}\}" )


class IHighlightChange(Protocol):
    def apply( self, text: str ) -> str: ...


def _wrap( color: str, inner: str ) -> str:
    return "{{highlight:" + color + "}}" + inner + "{{/highlight}}"


def _unique_span( text: str, inner: str ) -> re.Match:
    found = [ m for m in SPAN.finditer( text ) if m.group( 2 ) == inner ]
    if len( found ) != 1:
        raise NotesRefusal( f'the highlighted text "{ inner[ :60 ]}" ' +
                            ( "is not highlighted in the note" if not found else f"is highlighted { len( found )} times" ))
    return found[ 0 ]


class AddHighlight:
    def __init__( self, inner: str, color: str ) -> None:
        self._inner, self._color = inner, color

    def apply( self, text: str ) -> str:
        if "\n" in self._inner or "{{" in self._inner:
            raise NotesRefusal( "a highlight covers one line of plain text" )
        count = text.count( self._inner )
        if count != 1:
            raise NotesRefusal( f'the text "{ self._inner[ :60 ]}" ' + ( "is not in the note" if count == 0
                                                                          else f"occurs { count } times" ))
        at = text.index( self._inner )
        if any( m.start() < at + len( self._inner ) and at < m.end() for m in SPAN.finditer( text )):
            raise NotesRefusal( f'"{ self._inner[ :60 ]}" overlaps a highlight that is already there' )
        return text[ :at ] + _wrap( self._color, self._inner ) + text[ at + len( self._inner ):]


class RecolorHighlight:
    def __init__( self, inner: str, color: str ) -> None:
        self._inner, self._color = inner, color

    def apply( self, text: str ) -> str:
        m = _unique_span( text, self._inner )
        return text[ :m.start() ] + _wrap( self._color, self._inner ) + text[ m.end():]


class ClearHighlight:
    def __init__( self, inner: str ) -> None:
        self._inner = inner

    def apply( self, text: str ) -> str:
        m = _unique_span( text, self._inner )
        return text[ :m.start() ] + self._inner + text[ m.end():]


class ClearAllHighlights:
    def apply( self, text: str ) -> str:
        if not SPAN.search( text ):
            raise NotesRefusal( "the note has no highlights to clear" )
        return SPAN.sub( lambda m: m.group( 2 ), text )


def highlight_from( change: HighlightChange ) -> IHighlightChange:
    """Factory. Refuses a change that does not say enough, rather than guessing."""
    if change.action == "clear":
        if change.all:
            return ClearAllHighlights()
        if change.text is None:
            raise NotesRefusal( "clear needs the highlighted text, or all: true" )
        return ClearHighlight( change.text )
    if change.all or change.text is None or change.color is None:
        raise NotesRefusal( f"{ change.action } needs text and a colour" )
    return AddHighlight( change.text, change.color ) if change.action == "add" else RecolorHighlight( change.text, change.color )

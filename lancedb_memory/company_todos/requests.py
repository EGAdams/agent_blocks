"""Read one spoken request as a to-do command. No model: patterns, most specific first.

Add is tried before done on purpose: "add finish the taxes to the to-do list"
contains a done-word, and only the add pattern can say where the item's text
starts and stops. Anything that cannot be read with confidence is UNCLEAR, and
UNCLEAR changes nothing -- a to-do list that grows items nobody asked for is
worse than one that asks again.
"""
from __future__ import annotations

import re

from .models import TodoAction, TodoCommand

_LIST_NAME = r"(?:the\s+|our\s+|my\s+|a\s+)?(?:company\s+|office\s+)?(?:to[\s-]?do|task)(?:s|\s+list)?"
TODO_WORDS = re.compile( r"\bto[\s-]?do(?:s|\s+list)?\b|\btask\s+list\b", re.I )

_ADD_PATTERNS = (
    # "add X to the to-do list", "put X on our company todo list"
    re.compile( r"\b(?:add|put|write\s+down|stick|jot\s+down)\s+(?P<item>.+?)\s+(?:to|on|onto|in(?:to)?)\s+"
                + _LIST_NAME + r"\b", re.I ),
    # "add to the to-do list: X", "put on the todo list X"
    re.compile( r"\b(?:add|put|write)\s+(?:to|on|onto|in)\s+" + _LIST_NAME + r"\s*[:,\-]?\s+(?P<item>.+)$", re.I ),
    # "to-do: X", "new to-do: X"
    re.compile( r"\b(?:new\s+)?to[\s-]?do\s*:\s*(?P<item>.+)$", re.I ),
)
# What a pattern captures when the sentence named no item ("add to the to-do list").
_NOT_AN_ITEM = re.compile( r"(?:list|please|for\s+me|it|this|that|something|anything)", re.I )
_ADD_VERB    =re.compile( r"\b(?:add|put|write\s+down|jot\s+down)\b", re.I )
_DONE_VERB   = re.compile( r"\b(?:mark(?:ed)?|check(?:ed)?\s+off|cross(?:ed)?\s+off|tick(?:ed)?\s+off|"
                           r"complete[d]?|finish(?:ed)?|done)\b", re.I )
_NUMBER      = re.compile( r"(?:#|\bnumber\s+|\bitem\s+|\bno\.?\s*)(\d{1,4})\b", re.I )
_NEGATION    = re.compile( r"\b(?:don'?t|do\s+not|never|stop|cancel|not\s+yet|how\s+(?:do|does|can|would|to))\b", re.I )
_DONE_FILLER = re.compile( r"\b(?:please|can\s+you|could\s+you|mark(?:ed)?|check(?:ed)?|cross(?:ed)?|tick(?:ed)?|"
                           r"off|as|is|it|it'?s|complete[d]?|finish(?:ed)?|done|the|on|from|of|"
                           r"company|office|to[\s-]?do|task|list|item)\b", re.I )


def _tidy( text: str ) -> str:
    return text.strip().strip( "\"'" ).rstrip( ".!?," ).strip()


def read_request( message: str ) -> TodoCommand:
    text = " ".join( message.split())
    if not TODO_WORDS.search( text ):
        return TodoCommand( action=TodoAction.UNCLEAR, reason="the request doesn't mention the to-do list" )
    if _NEGATION.search( text ):
        return TodoCommand( action=TodoAction.UNCLEAR,
                            reason="it reads as a question about the list or a 'don't', not a change to make" )

    for pattern in _ADD_PATTERNS:
        if match := pattern.search( text ):
            item = _tidy( match.group( "item" ))
            if item and not TODO_WORDS.fullmatch( item ) and not _NOT_AN_ITEM.fullmatch( item ):
                return TodoCommand( action=TodoAction.ADD, text=item )
    if _ADD_VERB.search( text ):
        return TodoCommand( action=TodoAction.UNCLEAR, reason="I couldn't tell what item to add" )

    if _DONE_VERB.search( text ):
        if number := _NUMBER.search( text ):
            return TodoCommand( action=TodoAction.DONE, number=int( number.group( 1 )))
        named = " ".join( _DONE_FILLER.sub( " ", text ).split())
        if named:
            return TodoCommand( action=TodoAction.DONE, text=_tidy( named ))
        return TodoCommand( action=TodoAction.UNCLEAR, reason="I couldn't tell which item is done" )

    return TodoCommand( action=TodoAction.LIST )

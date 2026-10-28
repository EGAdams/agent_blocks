"""IConsequenceClassifier: does this step do something hard to undo?"""
from __future__ import annotations

import re

from ..models import RecordedAction

CONSEQUENTIAL_WORDS = ( "pay", "payment", "transfer", "send", "submit", "delete", "remove", "confirm",
                        "purchase", "place order", "buy", "checkout", "authorize", "approve", "withdraw",
                        "close account", "cancel order" )


class KeywordConsequenceClassifier:
    """Matches whole words in what the person saw (the element's text and
    labels). A missed match is caught by nobody, so the list leans wide; a
    false one costs one "yes" at playback."""

    def __init__( self, words: tuple[ str, ... ] = CONSEQUENTIAL_WORDS ) -> None:
        alternatives = "|".join( re.escape( w ).replace( r"\ ", r"\s+" ) for w in words )
        self._pattern = re.compile( rf"\b(?:{ alternatives })\b", re.IGNORECASE )

    def reason( self, action: RecordedAction ) -> str:
        if action.action_type not in ( "click", "press_key" ):
            return ""
        seen = [ str( action.metadata.get( "text", "" ))]
        seen += [ str( v ) for k, v in action.metadata.get( "field_hints", {}).items() if k in ( "label", "aria", "value" )]
        seen += [ l.value for l in action.locators if l.strategy in ( "text", "label" )]
        for text in seen:
            match = self._pattern.search( text )
            if match:
                return f"'{ match.group( 0 ) }' looks consequential"
        return ""

"""RuleChainInterpreter: IIntentInterpreter.

"I'm logged in. Open my statements." is two requests, so a message is split
into sentences first and each one read on its own. Pure rules: no model in
the path, so the same words always mean the same thing. A model-backed
interpreter can replace this one in `cashy.kit` without touching anything else.
"""
from __future__ import annotations

import re

from ..interfaces.conversation import IIntentRule
from ..models import Intent

_ADDRESS  = re.compile( r"^\s*(?:hey\s+|ok\s+|okay\s+)?cashy[,:!]?\s*", re.IGNORECASE )
_SENTENCE = re.compile( r"(?<=[.!?;])\s+|\s+(?:and then|then)\s+", re.IGNORECASE )


class RuleChainInterpreter:
    def __init__( self, rules: list[ IIntentRule ]) -> None:
        self._rules = rules

    def interpret( self, text: str ) -> list[ Intent ]:
        intents = []
        for sentence in _SENTENCE.split( _ADDRESS.sub( "", text.strip())):
            sentence = _ADDRESS.sub( "", sentence ).strip().rstrip( ".!?;" ).strip()
            if sentence:
                intents.append( self._one( sentence ))
        return intents or [ Intent( kind="unknown", text=text )]

    def _one( self, sentence: str ) -> Intent:
        for rule in self._rules:
            intent = rule.match( sentence )
            if intent is not None:
                return intent
        return Intent( kind="unknown", text=sentence )

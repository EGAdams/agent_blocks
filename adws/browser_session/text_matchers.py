"""ITextMatcher strategies: when does a button's text count as a match?"""
from __future__ import annotations


def _norm( text: str ) -> str:
    return " ".join(( text or "" ).split()).casefold()


class ExactTextMatcher:
    """Equal after trimming, collapsing whitespace and ignoring case.
    'Log In' matches ' Log In ' but not 'Log In with passkey'."""

    def __init__( self, wanted: str ):
        self._wanted = _norm( wanted )

    @property
    def label( self ) -> str:
        return f"exact:{ self._wanted }"

    def matches( self, text: str ) -> bool:
        return _norm( text ) == self._wanted


class ContainsTextMatcher:
    """The wanted words appear somewhere in the text, ignoring case."""

    def __init__( self, wanted: str ):
        self._wanted = _norm( wanted )

    @property
    def label( self ) -> str:
        return f"contains:{ self._wanted }"

    def matches( self, text: str ) -> bool:
        return self._wanted in _norm( text )

"""IElementLocator strategies, and the Chain of Responsibility over them."""
from __future__ import annotations

from .interfaces import IElementFinder, IElementLocator, IElementSpecification, IPageElement, ITextMatcher

#: What counts as "a button" when looking one up by its text.
BUTTON_LIKE = "button, [role='button'], input[type='submit']"


class CssLocator:
    """The first element matching a CSS selector that the spec accepts."""

    def __init__( self, selector: str, usable: IElementSpecification ):
        self._selector = selector
        self._usable   = usable

    @property
    def label( self ) -> str:
        return f"css:{ self._selector }"

    def locate( self, finder: IElementFinder ) -> IPageElement | None:
        return next(( e for e in finder.find( self._selector ) if self._usable.is_satisfied_by( e )), None )


class TextLocator:
    """The first element under `selector` whose text the matcher accepts
    and the spec calls usable."""

    def __init__( self, matcher: ITextMatcher, usable: IElementSpecification, selector: str = BUTTON_LIKE ):
        self._matcher  = matcher
        self._usable   = usable
        self._selector = selector

    @property
    def label( self ) -> str:
        return f"text:{ self._matcher.label }"

    def locate( self, finder: IElementFinder ) -> IPageElement | None:
        for element in finder.find( self._selector ):
            try:
                text = element.text or ""
            except Exception:
                continue        # a stale element is one candidate lost
            if self._matcher.matches( text ) and self._usable.is_satisfied_by( element ):
                return element
        return None


class FirstMatchLocator:
    """Chain of Responsibility: asks each locator in turn, first answer wins.
    Itself an IElementLocator (Composite), so a caller never knows whether
    it holds one way of finding or five."""

    def __init__( self, chain: list[ IElementLocator ]):
        self._chain  = chain
        self._tried: list[ str ] = []
        self._winner = ""

    @property
    def label( self ) -> str:
        return " -> ".join( l.label for l in self._chain )

    @property
    def tried( self ) -> list[ str ]:
        return list( self._tried )

    @property
    def winner( self ) -> str:
        return self._winner

    def locate( self, finder: IElementFinder ) -> IPageElement | None:
        self._tried, self._winner = [], ""
        for locator in self._chain:
            self._tried.append( locator.label )
            element = locator.locate( finder )
            if element is not None:
                self._winner = locator.label
                return element
        return None

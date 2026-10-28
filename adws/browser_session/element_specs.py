"""IElementSpecification: is this element one a person could click?

Specification pattern, with an AllOf composite, so "usable" is a sentence
assembled from parts rather than an `and` buried in a locator. A hidden
duplicate (Angular often renders one) must never win: clicking it does
nothing and reports success.
"""
from __future__ import annotations

from .interfaces import IElementSpecification, IPageElement


class DisplayedSpecification:
    def is_satisfied_by( self, element: IPageElement ) -> bool:
        try:
            return element.is_displayed()
        except Exception:       # a stale element is not a shown one
            return False


class EnabledSpecification:
    def is_satisfied_by( self, element: IPageElement ) -> bool:
        try:
            return element.is_enabled()
        except Exception:
            return False


class AllOfSpecification:
    """Composite: satisfied when every part is."""

    def __init__( self, parts: list[ IElementSpecification ]):
        self._parts = parts

    def is_satisfied_by( self, element: IPageElement ) -> bool:
        return all( p.is_satisfied_by( element ) for p in self._parts )


def clickable() -> IElementSpecification:
    """Shown and enabled -- the default meaning of 'usable'."""
    return AllOfSpecification([ DisplayedSpecification(), EnabledSpecification() ])

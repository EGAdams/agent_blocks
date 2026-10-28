"""ILocatorFactory as a Registry: each LocatorSpec `kind` maps to a builder.

A new way of finding things is a `register()` call in the composition root,
not an edit here -- open for extension, closed for modification.
"""
from __future__ import annotations

from .element_specs import clickable
from .interfaces import IElementLocator, IElementSpecification, ILocatorBuilder, ILocatorChain
from .locators import CssLocator, FirstMatchLocator, TextLocator
from .models import LocatorSpec
from .text_matchers import ContainsTextMatcher, ExactTextMatcher


class LocatorRegistry:

    def __init__( self ):
        self._builders: dict[ str, ILocatorBuilder ] = {}

    def register( self, kind: str, builder: ILocatorBuilder ) -> None:
        self._builders[ kind ] = builder

    def create( self, spec: LocatorSpec ) -> IElementLocator:
        builder = self._builders.get( spec.kind )
        if builder is None:
            raise KeyError( f"no locator registered for kind { spec.kind !r} (have { sorted( self._builders )})" )
        return builder( spec.value )

    def chain( self, specs: list[ LocatorSpec ]) -> ILocatorChain:
        return FirstMatchLocator([ self.create( s ) for s in specs ])


def default_registry( usable: IElementSpecification | None = None ) -> LocatorRegistry:
    """The three kinds a LocatorSpec can name today."""
    usable   = usable or clickable()
    registry = LocatorRegistry()
    registry.register( "button_text",     lambda v: TextLocator( ExactTextMatcher( v ), usable ))
    registry.register( "button_contains", lambda v: TextLocator( ContainsTextMatcher( v ), usable ))
    registry.register( "css",             lambda v: CssLocator( v, usable ))
    return registry

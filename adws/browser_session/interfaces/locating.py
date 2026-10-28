"""Finding one element: the locator, the chain of them, and their factory."""
from __future__ import annotations

from typing import Callable, Protocol

from ..models import LocatorSpec
from .page import IElementFinder, IPageElement


class IElementLocator( Protocol ):
    """Strategy: one way of finding an element."""

    @property
    def label( self ) -> str: ...
    def locate( self, finder: IElementFinder ) -> IPageElement | None: ...


class ILocatorChain( IElementLocator, Protocol ):
    """An IElementLocator that remembers which of its links it walked."""

    @property
    def tried( self ) -> list[ str ]: ...
    @property
    def winner( self ) -> str: ...


#: Builds one locator from a spec's `value`.
ILocatorBuilder = Callable[ [ str ], IElementLocator ]


class ILocatorFactory( Protocol ):
    """Factory Method: data (a LocatorSpec) in, behaviour (a locator) out.
    New kinds are registered, not edited in -- open for extension."""

    def register( self, kind: str, builder: ILocatorBuilder ) -> None: ...
    def create( self, spec: LocatorSpec ) -> IElementLocator: ...
    def chain( self, specs: list[ LocatorSpec ]) -> ILocatorChain: ...

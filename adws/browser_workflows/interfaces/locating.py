"""Finding a recorded element on today's page."""
from __future__ import annotations

from typing import Callable, Protocol

from ..models import LocatorCandidate, Resolution
from .browser import IElementQuery, IWorkflowElement


class ILocatorStrategy( Protocol ):
    """Strategy: every element one recorded candidate matches, visible or not."""

    def matches( self, query: IElementQuery, candidate: LocatorCandidate ) -> list[ IWorkflowElement ]: ...


#: Builds one strategy. Registered by name, so a new kind is a registration.
ILocatorStrategyBuilder = Callable[[], ILocatorStrategy ]


class ILocatorStrategyRegistry( Protocol ):
    """Factory Method: a candidate's `strategy` name in, its strategy out."""

    def register( self, name: str, builder: ILocatorStrategyBuilder ) -> None: ...

    def strategy( self, name: str ) -> ILocatorStrategy: ...

    def names( self ) -> list[ str ]: ...


class IElementResolver( Protocol ):
    """Chain of Responsibility over a step's candidates, in rank order: the
    first that finds exactly one usable element wins, and every attempt is
    reported, so a failure explains itself."""

    def resolve( self, query: IElementQuery,
                 candidates: list[ LocatorCandidate ]) -> tuple[ IWorkflowElement | None, Resolution ]: ...


class IElementWaiter( Protocol ):
    """Resolve, polling until found or the timeout runs out. No fixed delay."""

    def wait_for( self, query: IElementQuery, candidates: list[ LocatorCandidate ],
                  timeout_seconds: float ) -> tuple[ IWorkflowElement | None, Resolution ]: ...

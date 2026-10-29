"""Finding a recorded element on today's page.

Four Strategies (css, text, label, text_pattern) behind a registry, a resolver that asks
each candidate in rank order and takes the first that finds exactly one
usable element, and a waiter that keeps asking until a deadline. A candidate
that finds two elements is refused, not guessed between.
"""
from __future__ import annotations

import re

from browser_session.interfaces.system import IClock

from ..errors import BrowserWorkflowError
from ..interfaces.browser import IElementQuery, IWorkflowElement
from ..interfaces.locating import ILocatorStrategy, ILocatorStrategyBuilder, ILocatorStrategyRegistry
from ..models import LocatorAttempt, LocatorCandidate, Resolution

TEXT_SCOPE  = "a, button, input[type=submit], input[type=button], [role=button], [role=link], [role=menuitem], " \
              "[role=tab], label, summary, li, td, span, div"
LABEL_SCOPE = "input, select, textarea, button, [role=checkbox], [role=radio], [role=combobox], [role=textbox]"


def _norm( text: str | None ) -> str:
    return " ".join(( text or "" ).split()).casefold()


def candidate_label( candidate: LocatorCandidate ) -> str:
    return f"{ candidate.source }:{ candidate.strategy }={ candidate.value }"


class CssStrategy:
    def matches( self, query: IElementQuery, candidate: LocatorCandidate ) -> list[ IWorkflowElement ]:
        return query.find( candidate.value )


class TextStrategy:
    """Elements whose own visible text equals the recorded text. When an
    ancestor and its child both match, only the innermost counts."""

    def matches( self, query: IElementQuery, candidate: LocatorCandidate ) -> list[ IWorkflowElement ]:
        wanted = _norm( candidate.value )
        hits   = [ e for e in query.find( candidate.tag or TEXT_SCOPE ) if _norm( e.text ) == wanted ]
        if len( hits ) > 1:
            tagged = [ e for e in hits if e.tag_name.lower() in ( "a", "button", "input" )]
            hits   = tagged or hits
        return hits


class TextPatternStrategy:
    """Text with `*` standing for any run of characters, as a templated
    playback writes "Oct *, 2025 Opens menu to save document". Whole text
    only, so "Oct *" never matches "Not Oct 10"; two matches are refused."""

    def matches( self, query: IElementQuery, candidate: LocatorCandidate ) -> list[ IWorkflowElement ]:
        pattern = re.compile( ".*".join( re.escape( part ) for part in _norm( candidate.value ).split( "*" )))
        hits    = [ e for e in query.find( candidate.tag or TEXT_SCOPE ) if pattern.fullmatch( _norm( e.text ))]
        if len( hits ) > 1:
            tagged = [ e for e in hits if e.tag_name.lower() in ( "a", "button", "input" )]
            hits   = tagged or hits
        return hits


class LabelStrategy:
    """Controls whose aria-label, or whose <label>, says the recorded words."""

    def matches( self, query: IElementQuery, candidate: LocatorCandidate ) -> list[ IWorkflowElement ]:
        wanted = _norm( candidate.value )
        found  = [ e for e in query.find( candidate.tag or LABEL_SCOPE ) if _norm( e.get_attribute( "aria-label" )) == wanted ]
        for label in query.find( "label[for]" ):
            if _norm( label.text ) == wanted:
                target = label.get_attribute( "for" ) or ""
                found += [ e for e in query.find( f"[id=\"{ target }\"]" ) if e not in found ]
        return found


class LocatorStrategyRegistry:
    def __init__( self ) -> None:
        self._builders: dict[ str, ILocatorStrategyBuilder ] = {}

    def register( self, name: str, builder: ILocatorStrategyBuilder ) -> None:
        self._builders[ name ] = builder

    def strategy( self, name: str ) -> ILocatorStrategy:
        if name not in self._builders:
            raise BrowserWorkflowError( f"no locator strategy called { name!r}; known: { ', '.join( self.names())}" )
        return self._builders[ name ]()

    def names( self ) -> list[ str ]:
        return sorted( self._builders )


def default_strategy_registry() -> ILocatorStrategyRegistry:
    registry = LocatorStrategyRegistry()
    registry.register( "css", CssStrategy )
    registry.register( "text", TextStrategy )
    registry.register( "label", LabelStrategy )
    registry.register( "text_pattern", TextPatternStrategy )
    return registry


class RankedElementResolver:
    """IElementResolver. Each candidate, best rank first, is one link in the chain."""

    def __init__( self, registry: ILocatorStrategyRegistry ) -> None:
        self._registry = registry

    def resolve( self, query: IElementQuery,
                 candidates: list[ LocatorCandidate ]) -> tuple[ IWorkflowElement | None, Resolution ]:
        attempts: list[ LocatorAttempt ] = []
        for candidate in sorted( candidates, key=lambda c: c.rank ):
            label = candidate_label( candidate )
            try:
                elements = self._registry.strategy( candidate.strategy ).matches( query, candidate )
            except Exception as exc:                    # a bad selector is one failed link, not a crash
                attempts.append( LocatorAttempt( label=f"{ label } ({ type( exc ).__name__ })", outcome="error" ))
                continue
            outcome, element = _judge( elements )
            attempts.append( LocatorAttempt( label=label, outcome=outcome, count=len( elements )))
            if element is not None:
                return element, Resolution( found=True, winner=label, attempts=attempts )
        return None, Resolution( found=False, attempts=attempts )


def _judge( elements: list[ IWorkflowElement ]) -> tuple[ str, IWorkflowElement | None ]:
    if not elements:
        return "none", None
    shown = [ e for e in elements if safely( e.is_displayed )]
    if not shown:
        return "hidden", None
    # Selenium may call a zero-size icon displayed because its descendants
    # paint outside its box. WebDriver still cannot click that element.
    shown = [ e for e in shown if safely( lambda: _has_size( e ))]
    if not shown:
        return "zero_size", None
    if len( shown ) > 1:
        return "ambiguous", None
    if not safely( shown[ 0 ].is_enabled ):
        return "disabled", None
    return "found", shown[ 0 ]


def _has_size( element: IWorkflowElement ) -> bool:
    rect = element.rect
    return rect[ "width" ] > 0 and rect[ "height" ] > 0


def safely( check ) -> bool:
    try:
        return bool( check())
    except Exception:                                   # a stale element is simply not usable
        return False


class PollingElementWaiter:
    """IElementWaiter: resolve until found or out of time. The pause between
    tries is only the polling interval, never a fixed delay before acting."""

    def __init__( self, resolver: RankedElementResolver, clock: IClock, interval_seconds: float = 0.25 ) -> None:
        self._resolver = resolver
        self._clock    = clock
        self._interval = interval_seconds

    def wait_for( self, query: IElementQuery, candidates: list[ LocatorCandidate ],
                  timeout_seconds: float ) -> tuple[ IWorkflowElement | None, Resolution ]:
        deadline = self._clock.now() + timeout_seconds
        while True:
            element, resolution = self._resolver.resolve( query, candidates )
            if element is not None or self._clock.now() >= deadline:
                return element, resolution
            self._clock.sleep( self._interval )

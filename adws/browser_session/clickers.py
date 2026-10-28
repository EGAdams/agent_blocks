"""IClickAction: the Command, its Null Object, and a retrying Decorator."""
from __future__ import annotations

from .interfaces import IClickAction, IClock, IPageElement
from .models import ClickResult


class ElementClicker:
    """Clicks once. A failure is a result with a reason, never an exception."""

    def perform( self, element: IPageElement ) -> ClickResult:
        try:
            element.click()
        except Exception as e:
            return ClickResult( clicked=False, reason=f"{ type( e ).__name__ }: { e }".strip())
        return ClickResult( clicked=True )


class DryRunClicker:
    """Null Object: finds-but-does-not-press. Everything before it really ran."""

    def perform( self, element: IPageElement ) -> ClickResult:
        return ClickResult( clicked=False, dry_run=True, reason="dry run: found it, did not click" )


class RetryingClickAction:
    """Decorator: tries the inner click up to `attempts` times.

    Angular sign-in pages paint a spinner or a cookie banner over the button
    for a moment, and Selenium reports that as a failed click. A short wait
    and one more try is the whole fix, and it belongs around the Command, not
    inside it.
    """

    def __init__( self, inner: IClickAction, clock: IClock, attempts: int = 3, pause_seconds: float = 0.5 ):
        self._inner    = inner
        self._clock    = clock
        self._attempts = max( 1, attempts )
        self._pause    = pause_seconds

    def perform( self, element: IPageElement ) -> ClickResult:
        result = self._inner.perform( element )
        for _ in range( self._attempts - 1 ):
            if result.clicked or result.dry_run:
                break
            self._clock.sleep( self._pause )
            result = self._inner.perform( element )
        return result

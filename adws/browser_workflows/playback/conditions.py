"""Explicit waits: one IConditionCheck per WaitKind, and a waiter that polls
them until each holds or its own timeout runs out. No fixed sleeps."""
from __future__ import annotations

from browser_session.interfaces.system import IClock

from ..interfaces.browser import IBrowserAdapter
from ..interfaces.playback import IConditionCheck
from ..models import WaitCondition
from .locating import safely


class UrlEqualsCheck:
    def holds( self, browser: IBrowserAdapter, value: str ) -> bool:
        return browser.current_url.rstrip( "/" ) == value.rstrip( "/" )


class UrlContainsCheck:
    def holds( self, browser: IBrowserAdapter, value: str ) -> bool:
        return value in browser.current_url


class TextPresentCheck:
    def holds( self, browser: IBrowserAdapter, value: str ) -> bool:
        return value.casefold() in browser.body_text().casefold()


class NavigationCompleteCheck:
    def holds( self, browser: IBrowserAdapter, value: str ) -> bool:
        return browser.ready_state() == "complete"


class ElementVisibleCheck:
    """`value` is a CSS selector; one displayed match is enough."""

    def holds( self, browser: IBrowserAdapter, value: str ) -> bool:
        return any( safely( e.is_displayed ) for e in browser.find( value ))


class ElementEnabledCheck:
    def holds( self, browser: IBrowserAdapter, value: str ) -> bool:
        return any( safely( e.is_displayed ) and safely( e.is_enabled ) for e in browser.find( value ))


def default_condition_checks() -> dict[ str, IConditionCheck ]:
    return { "url_equals": UrlEqualsCheck(), "url_contains": UrlContainsCheck(),
             "text_present": TextPresentCheck(), "navigation_complete": NavigationCompleteCheck(),
             "element_visible": ElementVisibleCheck(), "element_enabled": ElementEnabledCheck() }


class PollingConditionWaiter:
    """IConditionWaiter. Conditions are waited for in order, each with its own timeout."""

    def __init__( self, checks: dict[ str, IConditionCheck ], clock: IClock, interval_seconds: float = 0.25 ) -> None:
        self._checks   = checks
        self._clock    = clock
        self._interval = interval_seconds

    def wait( self, browser: IBrowserAdapter, conditions: list[ WaitCondition ]) -> str:
        for condition in conditions:
            check = self._checks.get( condition.kind )
            if check is None:
                return f"no check for wait kind { condition.kind!r}"
            deadline = self._clock.now() + condition.timeout_seconds
            while not self._holds( check, browser, condition.value ):
                if self._clock.now() >= deadline:
                    target = f" { condition.value!r}" if condition.value else ""
                    return f"waited { condition.timeout_seconds:g}s for { condition.kind }{ target }"
                self._clock.sleep( self._interval )
        return ""

    @staticmethod
    def _holds( check: IConditionCheck, browser: IBrowserAdapter, value: str ) -> bool:
        try:
            return check.holds( browser, value )
        except Exception:                               # mid-navigation the page can vanish under a check
            return False

"""IClickPartsFactory: today's tab selector, locator chain and clicker.
Swapping any of them is a change to what this is constructed with."""
from __future__ import annotations

from .clickers import DryRunClicker, ElementClicker, RetryingClickAction
from .interfaces import IClickAction, IClock, ILocatorChain, ILocatorFactory, ITabSelector
from .models import ClickTarget
from .tab_selector import SpecificationTabSelector
from .url_specs import AllOfUrlSpecification, HostSuffixSpecification, HttpsSpecification


class DefaultClickPartsFactory:

    def __init__( self, locators: ILocatorFactory, clock: IClock, click_attempts: int = 3 ):
        self._locators       = locators
        self._clock          = clock
        self._click_attempts = click_attempts

    def tab_selector( self, target: ClickTarget ) -> ITabSelector:
        return SpecificationTabSelector( AllOfUrlSpecification([
            HttpsSpecification(), HostSuffixSpecification( target.host_suffix )]))

    def locator( self, target: ClickTarget ) -> ILocatorChain:
        return self._locators.chain( target.locators )

    def click_action( self, dry_run: bool ) -> IClickAction:
        if dry_run:
            return DryRunClicker()
        return RetryingClickAction( ElementClicker(), self._clock, attempts=self._click_attempts )

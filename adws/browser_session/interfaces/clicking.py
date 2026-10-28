"""The click, and the Abstract Factory that assembles a click run's parts."""
from __future__ import annotations

from typing import Protocol

from ..models import ClickResult, ClickTarget
from .locating import ILocatorChain
from .page import IPageElement
from .selecting import ITabSelector


class IClickAction( Protocol ):
    """Command: do the click (or, as a Null Object, don't). Never raises."""

    def perform( self, element: IPageElement ) -> ClickResult: ...


class IClickPartsFactory( Protocol ):
    """Abstract Factory: every part of a click run that depends on the
    target or the options, so the orchestrator never names a concrete."""

    def tab_selector( self, target: ClickTarget ) -> ITabSelector: ...
    def locator( self, target: ClickTarget ) -> ILocatorChain: ...
    def click_action( self, dry_run: bool ) -> IClickAction: ...

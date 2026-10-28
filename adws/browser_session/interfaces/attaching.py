"""Getting hold of a Chrome that is already running.

Three roles kept apart on purpose, because each changes for its own reason:
building a Selenium driver (Selenium's API), wrapping it in selenium-headless's
WebBrowserManager (that repo's API), and letting go afterwards (a safety rule).
"""
from __future__ import annotations

from typing import Protocol

from ..models import DebugEndpoint
from .page import IBrowserSession, IPageElement


class ISwitchTo( Protocol ):
    def window( self, handle: str ) -> None: ...


class IDriverService( Protocol ):
    def stop( self ) -> None: ...


class IWindowDriver( Protocol ):
    """The slice of a Selenium WebDriver used here."""

    @property
    def window_handles( self ) -> list[ str ]: ...
    @property
    def current_window_handle( self ) -> str: ...
    @property
    def current_url( self ) -> str: ...
    @property
    def switch_to( self ) -> ISwitchTo: ...
    @property
    def service( self ) -> IDriverService: ...


class IBrowserManager( Protocol ):
    """The slice of selenium-headless's WebBrowserManager used here."""

    driver: IWindowDriver

    def find_elements( self, css_selector: str ) -> list[ IPageElement ]: ...


class IDriverFactory( Protocol ):
    """Builds a driver connected to an already-running browser."""

    def attached( self, endpoint: DebugEndpoint ) -> IWindowDriver: ...


class IBrowserManagerFactory( Protocol ):
    """Wraps a ready driver in a browser manager."""

    def wrap( self, driver: IWindowDriver ) -> IBrowserManager: ...


class IDetachPolicy( Protocol ):
    """Strategy: how to let go of a driver. Never by closing the browser."""

    def detach( self, driver: IWindowDriver ) -> None: ...


class IBrowserAttacher( Protocol ):
    def attach( self, endpoint: DebugEndpoint ) -> IBrowserSession: ...

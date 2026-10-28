"""The values that cross between browser_session's pieces. Data only."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DebugEndpoint( BaseModel ):
    """Where a debuggable Chrome's DevTools port listens.

    127.0.0.1 is reachable from WSL because this box runs WSL with
    `networkingMode=mirrored`; the port is bound to loopback only by Chrome.
    """
    model_config = ConfigDict( frozen=True )

    host: str = "127.0.0.1"
    port: int = 9222

    @property
    def address( self ) -> str:
        return f"{ self.host }:{ self.port }"


class LocatorSpec( BaseModel ):
    """One way of finding an element, as data. `locators.build_locator` turns
    it into behaviour, so a site entry never names a class."""
    model_config = ConfigDict( frozen=True )

    kind:  Literal[ "button_text", "button_contains", "css" ]
    value: str


class ClickTarget( BaseModel ):
    """A button worth pressing: which site's tab, and how to recognise it.

    `locators` are tried in order and the first that finds a visible,
    enabled element wins -- the most specific way first, the sturdiest
    fallback last.
    """
    model_config = ConfigDict( frozen=True )

    name:        str
    description: str
    host_suffix: str
    locators:    list[ LocatorSpec ] = Field( min_length=1 )


class TabInfo( BaseModel ):
    handle: str
    url:    str


class EndpointCheck( BaseModel ):
    reachable: bool
    browser:   str = ""
    reason:    str = ""


class TabChoice( BaseModel ):
    found:  bool
    handle: str = ""
    url:    str = ""
    seen:   list[ str ] = Field( default_factory=list )


class LocateResult( BaseModel ):
    found:    bool
    strategy: str = ""            # which locator found it, e.g. "button_text:Log In"
    tried:    list[ str ] = Field( default_factory=list )


class ClickResult( BaseModel ):
    clicked: bool
    dry_run: bool = False
    reason:  str = ""


class ChromeLaunchResult( BaseModel ):
    launched: bool
    command:  list[ str ] = Field( default_factory=list )
    reason:   str = ""

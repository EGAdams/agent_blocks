"""The browser as Cashy needs it: playback's IBrowserAdapter plus the moves a
person steering by conversation asks for."""
from __future__ import annotations

from typing import Protocol

from browser_workflows.interfaces.browser import IBrowserAdapter


class ICashyBrowser( IBrowserAdapter, Protocol ):
    @property
    def target_id( self ) -> str: ...

    def go_back( self ) -> None: ...

    def open_tab( self, url: str ) -> None: ...


class IBrowserConnection( Protocol ):
    """Attaches to the debug Chrome for one turn, preferring the session's tab
    (or one on the bank's hosts). Never closes a browser; only
    `LaunchingConnection` ever starts one, and only the separate debug one."""

    def attach( self, target_id: str, hosts: list[ str ]) -> ICashyBrowser: ...


class IChromeLauncher( Protocol ):
    """Starts the debug Chrome (its own profile, loopback DevTools port).
    Returns a line saying what happened; never raises."""

    def launch( self ) -> str: ...

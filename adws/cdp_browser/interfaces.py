"""What the CDP pieces need from each other, as Protocols."""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from browser_session.models import DebugEndpoint


class CdpTarget( BaseModel ):
    """One DevTools target, as /json/list describes it."""
    id:    str
    type:  str = "page"
    url:   str = ""
    title: str = ""
    webSocketDebuggerUrl: str = ""


class ICdpChannel( Protocol ):
    """One target's command stream: send a method, get its result."""

    def call( self, method: str, params: dict[ str, Any ] | None = None,
              timeout_seconds: float = 30.0 ) -> dict[ str, Any ]: ...

    def close( self ) -> None: ...


class ICdpConnector( Protocol ):
    """Opens a channel on a target's (or the browser's) websocket URL."""

    def connect( self, websocket_url: str ) -> ICdpChannel: ...


class ITargetDirectory( Protocol ):
    """The debug Chrome's targets, over its HTTP endpoints."""

    def pages( self, endpoint: DebugEndpoint ) -> list[ CdpTarget ]: ...

    def browser_socket( self, endpoint: DebugEndpoint ) -> str: ...

    def open_page( self, endpoint: DebugEndpoint, url: str ) -> CdpTarget: ...

    def activate( self, endpoint: DebugEndpoint, target_id: str ) -> None: ...


class ITargetChooser( Protocol ):
    """Which open tab a session should drive."""

    def choose( self, pages: list[ CdpTarget ]) -> CdpTarget | None: ...


class IEndpointGuard( Protocol ):
    """Refuses a debug address this machine should not be driving."""

    def check( self, endpoint: DebugEndpoint ) -> None: ...

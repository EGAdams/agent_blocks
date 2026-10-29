"""The debug Chrome's HTTP side: list its tabs, open one, bring one forward.

`LoopbackOnly` is the security boundary for all of it. The DevTools port
can drive a signed-in bank session, so it is only ever spoken to on this
machine; a websocket URL Chrome hands back is pinned to the same loopback
address it was asked on.
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import quote, urlparse

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError

from .interfaces import CdpTarget, IEndpointGuard

LOOPBACK = frozenset({ "127.0.0.1", "localhost", "::1" })

#: Tabs a person is not looking at as a web page.
NOT_A_PAGE = ( "devtools://", "chrome-extension://", "chrome://", "chrome-untrusted://" )


class LoopbackOnly:
    """IEndpointGuard."""

    def check( self, endpoint: DebugEndpoint ) -> None:
        if endpoint.host not in LOOPBACK:
            raise BrowserWorkflowError( f"refusing to drive a DevTools port off this machine ({ endpoint.host })" )


def pinned( websocket_url: str, endpoint: DebugEndpoint ) -> str:
    """The same path, on the loopback address and port we asked on."""
    parts = urlparse( websocket_url )
    return f"ws://{ endpoint.host }:{ endpoint.port }{ parts.path }"


class HttpTargetDirectory:
    """ITargetDirectory over /json/list, /json/version, /json/new, /json/activate."""

    def __init__( self, guard: IEndpointGuard, timeout_seconds: float = 5.0 ) -> None:
        self._guard   = guard
        self._timeout = timeout_seconds

    def pages( self, endpoint: DebugEndpoint ) -> list[ CdpTarget ]:
        found = [ CdpTarget.model_validate( t ) for t in self._get( endpoint, "/json/list" )]
        return [ t for t in found if t.type == "page" and not t.url.startswith( NOT_A_PAGE )]

    def browser_socket( self, endpoint: DebugEndpoint ) -> str:
        return pinned( self._get( endpoint, "/json/version" )[ "webSocketDebuggerUrl" ], endpoint )

    def open_page( self, endpoint: DebugEndpoint, url: str ) -> CdpTarget:
        return CdpTarget.model_validate( self._get( endpoint, f"/json/new?{ quote( url, safe='' )}", method="PUT" ))

    def activate( self, endpoint: DebugEndpoint, target_id: str ) -> None:
        self._get( endpoint, f"/json/activate/{ target_id }", parse=False )

    def _get( self, endpoint: DebugEndpoint, path: str, method: str = "GET", parse: bool = True ):
        self._guard.check( endpoint )
        request = urllib.request.Request( f"http://{ endpoint.address }{ path }", method=method )
        try:
            with urllib.request.urlopen( request, timeout=self._timeout ) as response:
                body = response.read().decode( "utf-8" )
        except OSError as exc:
            raise BrowserWorkflowError( f"no debug Chrome answered at { endpoint.address } ({ exc }); "
                                        "start it with adw_interface_browser_debug_open.py" ) from exc
        return json.loads( body ) if parse else body

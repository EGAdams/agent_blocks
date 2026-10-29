"""WebSocketCdpChannel: ICdpChannel over one StdlibWebSocket.

Commands are numbered; events that arrive while waiting for a reply are
kept (the newest few hundred) for whoever asks with `events()`.
"""
from __future__ import annotations

import json
import socket
import time
from collections import deque
from typing import Any

from browser_workflows.errors import BrowserWorkflowError

from .interfaces import ICdpChannel
from .websocket import StdlibWebSocket


class CdpCommandError( BrowserWorkflowError ):
    """Chrome answered a command with an error."""


class WebSocketCdpChannel:
    def __init__( self, socket_: StdlibWebSocket ) -> None:
        self._ws     = socket_
        self._next   = 0
        self._events: deque[ dict[ str, Any ]] = deque( maxlen=500 )

    def call( self, method: str, params: dict[ str, Any ] | None = None,
              timeout_seconds: float = 30.0 ) -> dict[ str, Any ]:
        self._next += 1
        wanted = self._next
        self._ws.send_text( json.dumps({ "id": wanted, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout_seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrowserWorkflowError( f"Chrome did not answer { method } within { timeout_seconds:.0f }s" )
            self._ws.settimeout( left )
            try:
                message = json.loads( self._ws.recv_text())
            except socket.timeout:
                continue
            if message.get( "id" ) == wanted:
                if "error" in message:
                    raise CdpCommandError( f"{ method }: { message[ 'error' ].get( 'message', message[ 'error' ]) }" )
                return message.get( "result", {})
            if "method" in message:
                self._events.append( message )

    def events( self ) -> list[ dict[ str, Any ]]:
        return list( self._events )

    def close( self ) -> None:
        self._ws.close()


class WebSocketCdpConnector:
    """ICdpConnector."""

    def __init__( self, timeout_seconds: float = 10.0 ) -> None:
        self._timeout = timeout_seconds

    def connect( self, websocket_url: str ) -> ICdpChannel:
        return WebSocketCdpChannel( StdlibWebSocket( websocket_url, self._timeout ))

"""IExtensionLink over one aiohttp WebSocket: request/reply by request_id."""
from __future__ import annotations

import asyncio
import itertools
from typing import Any, Protocol


class IJsonSocket( Protocol ):
    """The slice of aiohttp's WebSocketResponse the link uses."""

    @property
    def closed( self ) -> bool: ...

    async def send_json( self, data: Any ) -> None: ...


class WebSocketExtensionLink:
    """Holds the extension's one connection. A newer connection replaces an
    older one (a restarted service worker reconnects; the old socket is dead)."""

    def __init__( self ) -> None:
        self._socket: IJsonSocket | None = None
        self._waiting: dict[ str, asyncio.Future ] = {}
        self._ids = itertools.count( 1 )

    @property
    def connected( self ) -> bool:
        return self._socket is not None and not self._socket.closed

    def attach( self, socket: IJsonSocket ) -> None:
        self._socket = socket

    def detach( self, socket: IJsonSocket ) -> None:
        if self._socket is socket:
            self._socket = None
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception( ConnectionError( "the extension disconnected" ))
            self._waiting.clear()

    def deliver( self, message: dict[ str, Any ]) -> bool:
        """A message from the extension. True if it answered a request."""
        future = self._waiting.pop( str( message.get( "request_id", "" )), None )
        if future is None or future.done():
            return False
        future.set_result( message )
        return True

    async def request( self, message: dict[ str, Any ], reply_type: str, timeout_seconds: float ) -> dict[ str, Any ]:
        if not self.connected:
            raise ConnectionError( "the extension is not connected" )
        request_id = str( next( self._ids ))
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[ request_id ] = future
        await self._socket.send_json({ **message, "request_id": request_id, "reply_type": reply_type })
        try:
            return await asyncio.wait_for( future, timeout_seconds )
        except asyncio.TimeoutError as exc:
            raise TimeoutError( f"no { reply_type } within { timeout_seconds:g }s" ) from exc
        finally:
            self._waiting.pop( request_id, None )

    async def notify( self, message: dict[ str, Any ]) -> None:
        if self.connected:
            await self._socket.send_json( message )

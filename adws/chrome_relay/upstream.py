"""UpstreamBrowser: the relay's one connection to the everyday Chrome.

Connected on first use (in the background, see `_ensure`) and kept; Chrome shows an Allow box for each new connection, so this is the only one.
`call` numbers a command, waits for its reply and returns the whole reply message (result or error). Every
other message (an event) is handed to each listener. When Chrome goes away the pending calls fail, listeners
hear `{"relay": "closed"}`, and the next call connects again (and Chrome asks again).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .locate import ChromeNotReachable

log = logging.getLogger( "chrome_relay" )

Listener = Callable[[ dict[ str, Any ]], None ]


class UpstreamBrowser:
    def __init__( self, locate: Callable[[], str ], connect_seconds: float = 90.0, answer_seconds: float = 2.5 ) -> None:
        self._locate   = locate
        self._connect  = connect_seconds
        self._answer   = answer_seconds
        self._connecting: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._next     = 0
        self._pending: dict[ int, asyncio.Future ] = {}
        self._listeners: list[ Listener ] = []

    @property
    def connected( self ) -> bool:
        return self._ws is not None and not self._ws.closed

    def listen( self, listener: Listener ) -> Callable[[], None ]:
        self._listeners.append( listener )
        return lambda: self._listeners.remove( listener ) if listener in self._listeners else None

    async def call( self, method: str, params: dict[ str, Any ] | None = None, session_id: str = "",
                    timeout_seconds: float = 60.0 ) -> dict[ str, Any ]:
        await self._ensure()
        self._next += 1
        wanted = self._next
        message: dict[ str, Any ] = { "id": wanted, "method": method, "params": params or {}}
        if session_id:
            message[ "sessionId" ] = session_id
        future = asyncio.get_running_loop().create_future()
        self._pending[ wanted ] = future
        try:
            await self._ws.send_str( json.dumps( message ))
            return await asyncio.wait_for( future, timeout_seconds )
        except ( ConnectionError, aiohttp.ClientError ) as exc:
            raise ChromeNotReachable( f"the connection to Chrome dropped ({ type( exc ).__name__ })" ) from None
        except asyncio.TimeoutError:
            raise ChromeNotReachable( f"Chrome did not answer { method } within { round( timeout_seconds ) }s "
                                      "(is an Allow box waiting?)" ) from None
        finally:
            self._pending.pop( wanted, None )

    async def result( self, method: str, params: dict[ str, Any ] | None = None ) -> dict[ str, Any ]:
        """A browser-level call's result; LookupError carries Chrome's own error, ChromeNotReachable a lost Chrome."""
        reply = await self.call( method, params )
        if "error" in reply:
            raise LookupError( str( reply[ "error" ].get( "message", reply[ "error" ])))
        return reply.get( "result", {})

    async def close( self ) -> None:
        if self._connecting is not None and not self._connecting.done():
            self._connecting.cancel()
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            await asyncio.gather( self._reader, return_exceptions=True )
        if self._http is not None:
            await self._http.close()

    # -- the connection -------------------------------------------------------------------------------

    async def _ensure( self ) -> None:
        """Connect in the background and wait a moment: Chrome holds the connection until Allow is clicked, and
        Cashy's HTTP calls give up after a few seconds, so a slow Allow is a quick answer saying so."""
        if self.connected:
            return
        if self._connecting is None or self._connecting.done():
            self._connecting = asyncio.create_task( self._connect_once())
            self._connecting.add_done_callback( lambda t: t.cancelled() or t.exception())
        try:
            await asyncio.wait_for( asyncio.shield( self._connecting ), self._answer )
        except asyncio.TimeoutError:
            raise ChromeNotReachable( "Chrome is asking whether to allow remote debugging: click Allow in Chrome "
                                      "on this computer, then ask again" ) from None

    async def _connect_once( self ) -> None:
        url = self._locate()
        if self._http is None:
            self._http = aiohttp.ClientSession()
        log.info( "connecting to the everyday Chrome on port %s (Chrome asks for Allow)", url.split( ":" )[ 2 ].split( "/" )[ 0 ])
        try:
            # No Origin header, unlimited message size (a whole page's DOM can come back), Chrome's own pings.
            ws = await asyncio.wait_for( self._http.ws_connect( url, max_msg_size=0, autoping=True ), self._connect )
        except ( aiohttp.ClientError, asyncio.TimeoutError, OSError ) as exc:
            log.info( "Chrome did not accept the connection (%s)", type( exc ).__name__ )
            raise ChromeNotReachable( f"Chrome refused or did not answer the DevTools connection "
                                      f"({ type( exc ).__name__ }); click Allow in Chrome, or turn on "
                                      "chrome://inspect/#remote-debugging" ) from None
        self._ws = ws
        self._reader = asyncio.create_task( self._read( ws ))
        log.info( "connected" )

    async def _read( self, ws: aiohttp.ClientWebSocketResponse ) -> None:
        try:
            async for frame in ws:
                if frame.type != aiohttp.WSMsgType.TEXT:
                    continue
                message = json.loads( frame.data )
                waiting = self._pending.get( message.get( "id" )) if "id" in message else None
                if waiting is not None:
                    if not waiting.done():
                        waiting.set_result( message )
                    continue
                for listener in list( self._listeners ):
                    listener( message )
        finally:
            log.info( "the everyday Chrome connection closed" )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception( ConnectionError( "Chrome closed the connection" ))
            for listener in list( self._listeners ):
                listener({ "relay": "closed" })

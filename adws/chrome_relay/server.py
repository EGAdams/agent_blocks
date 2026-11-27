"""The relay's loopback face: the endpoints a `--remote-debugging-port` Chrome serves, over one UpstreamBrowser.

    GET  /json/version            answered without touching Chrome (a liveness probe must not raise an Allow box)
    GET  /json, /json/list        Target.getTargets
    PUT  /json/new?<url>          Target.createTarget
    GET  /json/activate/<id>      Target.activateTarget
    GET  /json/close/<id>         Target.closeTarget (one tab; the browser is never closed)
    WS   /devtools/browser/<any>  browser-level commands, renumbered
    WS   /devtools/page/<id>      a flattened session on that tab
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import unquote

from aiohttp import WSMsgType, web

from .locate import ChromeNotReachable
from .upstream import UpstreamBrowser

log = logging.getLogger( "chrome_relay" )

LOOPBACK_HOSTS = ( "127.0.0.1", "localhost", "[::1]" )


def _one_line( text: str ) -> str:
    return " ".join( text.split())[ :200 ].encode( "ascii", "replace" ).decode()


@web.middleware
async def loopback_only( request: web.Request, handler ):
    """No web page (it sends Origin) and no rebound hostname (its Host isn't loopback) is ever served."""
    if request.headers.get( "Origin" ):
        log.warning( "refused a request with an Origin header" )
        raise web.HTTPForbidden( reason="requests from web pages are refused" )
    host = request.headers.get( "Host", "" ).rsplit( ":", 1 )[ 0 ]
    if host not in LOOPBACK_HOSTS:
        log.warning( "refused a request for host %r", host[ :60 ])
        raise web.HTTPForbidden( reason="loopback only" )
    return await handler( request )


class Relay:
    def __init__( self, upstream: UpstreamBrowser ) -> None:
        self._up = upstream
        self._page_sessions: set[ str ] = set()
        self._browser_clients: set[ web.WebSocketResponse ] = set()
        upstream.listen( self._browser_event )

    def app( self ) -> web.Application:
        app = web.Application( middlewares=[ loopback_only ])
        app.router.add_get( "/json/version", self.version )
        app.router.add_get( "/json", self.targets )
        app.router.add_get( "/json/list", self.targets )
        app.router.add_put( "/json/new", self.new )
        app.router.add_get( "/json/activate/{id}", self.activate )
        app.router.add_get( "/json/close/{id}", self.close_tab )
        app.router.add_get( "/devtools/browser/{rest:.*}", self.browser_socket )
        app.router.add_get( "/devtools/page/{id}", self.page_socket )
        return app

    # -- HTTP ------------------------------------------------------------------------------------------

    async def version( self, request: web.Request ) -> web.Response:
        return web.json_response({ "Browser": "everyday Chrome (relay)", "Protocol-Version": "1.3",
                                   "Relay-Connected": self._up.connected,
                                   "webSocketDebuggerUrl": f"ws://{ request.host }/devtools/browser/relay" })

    async def targets( self, request: web.Request ) -> web.Response:
        infos = ( await self._chrome( "Target.getTargets" ))[ "targetInfos" ]
        return web.json_response([ self._described( request, t ) for t in infos ])

    async def new( self, request: web.Request ) -> web.Response:
        url = unquote( request.query_string ) or "about:blank"
        target = ( await self._chrome( "Target.createTarget", { "url": url }))[ "targetId" ]
        infos = ( await self._chrome( "Target.getTargets" ))[ "targetInfos" ]
        info = next(( t for t in infos if t[ "targetId" ] == target ), { "targetId": target, "type": "page", "url": url, "title": "" })
        log.info( "opened tab %s", target )
        return web.json_response( self._described( request, info ))

    async def activate( self, request: web.Request ) -> web.Response:
        await self._chrome( "Target.activateTarget", { "targetId": request.match_info[ "id" ]}, missing=True )
        return web.Response( text="Target activated" )

    async def close_tab( self, request: web.Request ) -> web.Response:
        await self._chrome( "Target.closeTarget", { "targetId": request.match_info[ "id" ]}, missing=True )
        log.info( "closed tab %s", request.match_info[ "id" ])
        return web.Response( text="Target is closing" )

    async def _chrome( self, method: str, params: dict[ str, Any ] | None = None, missing: bool = False ) -> dict[ str, Any ]:
        try:
            return await self._up.result( method, params )
        except ChromeNotReachable as exc:
            raise web.HTTPServiceUnavailable( reason=_one_line( f"everyday Chrome: { exc }" )) from None
        except LookupError as exc:
            if missing:
                raise web.HTTPNotFound( reason=_one_line( f"No such target id ({ exc })" )) from None
            raise web.HTTPBadGateway( reason=_one_line( f"{ method }: { exc }" )) from None

    @staticmethod
    def _described( request: web.Request, info: dict[ str, Any ]) -> dict[ str, Any ]:
        return { "id": info[ "targetId" ], "type": info.get( "type", "" ), "url": info.get( "url", "" ),
                 "title": info.get( "title", "" ),
                 "webSocketDebuggerUrl": f"ws://{ request.host }/devtools/page/{ info[ 'targetId' ] }" }

    # -- websockets ------------------------------------------------------------------------------------

    async def browser_socket( self, request: web.Request ) -> web.WebSocketResponse:
        ws = web.WebSocketResponse( max_msg_size=0 )
        await ws.prepare( request )
        self._browser_clients.add( ws )
        log.info( "browser client connected" )
        try:
            await self._pump( ws, "" )
        finally:
            self._browser_clients.discard( ws )
            log.info( "browser client gone" )
        return ws

    async def page_socket( self, request: web.Request ) -> web.WebSocketResponse:
        target = request.match_info[ "id" ]
        try:
            session = ( await self._up.result( "Target.attachToTarget", { "targetId": target, "flatten": True }))[ "sessionId" ]
        except ChromeNotReachable as exc:
            raise web.HTTPServiceUnavailable( reason=_one_line( f"everyday Chrome: { exc }" )) from None
        except LookupError as exc:
            raise web.HTTPNotFound( reason=_one_line( f"No such target id ({ exc })" )) from None
        ws = web.WebSocketResponse( max_msg_size=0 )
        await ws.prepare( request )
        self._page_sessions.add( session )
        log.info( "page client on tab %s", target )
        loop = asyncio.get_running_loop()

        def mine( message: dict[ str, Any ]) -> None:
            if message.get( "relay" ) == "closed" or (
                    message.get( "method" ) == "Target.detachedFromTarget"
                    and message.get( "params", {}).get( "sessionId" ) == session ):
                loop.create_task( ws.close())
            elif message.get( "sessionId" ) == session:
                loop.create_task( self._send( ws, { k: v for k, v in message.items() if k != "sessionId" }))

        stop = self._up.listen( mine )
        try:
            await self._pump( ws, session )
        finally:
            stop()
            self._page_sessions.discard( session )
            if self._up.connected:
                try:
                    await self._up.call( "Target.detachFromTarget", { "sessionId": session }, timeout_seconds=5 )
                except ChromeNotReachable:
                    pass
            log.info( "page client on tab %s gone", target )
        return ws

    async def _pump( self, ws: web.WebSocketResponse, session: str ) -> None:
        """Each command the client sends goes upstream under a fresh id; its reply comes back under the client's."""
        pending: set[ asyncio.Task ] = set()
        async for frame in ws:
            if frame.type != WSMsgType.TEXT:
                continue
            try:
                message = json.loads( frame.data )
                theirs, method = message[ "id" ], str( message[ "method" ])
            except ( ValueError, KeyError, TypeError ):
                continue
            task = asyncio.create_task( self._forward( ws, theirs, method, message.get( "params" ) or {}, session ))
            pending.add( task )
            task.add_done_callback( pending.discard )
        for task in pending:
            task.cancel()

    async def _forward( self, ws: web.WebSocketResponse, theirs: Any, method: str, params: dict, session: str ) -> None:
        try:
            reply = await self._up.call( method, params, session_id=session, timeout_seconds=120 )
        except ChromeNotReachable as exc:
            reply = { "error": { "code": -32000, "message": f"everyday Chrome: { exc }" }}
        reply = { k: v for k, v in reply.items() if k != "sessionId" }
        reply[ "id" ] = theirs
        await self._send( ws, reply )

    def _browser_event( self, message: dict[ str, Any ]) -> None:
        """A browser-level event (or one for a session no page client owns) goes to every browser client."""
        if message.get( "relay" ) == "closed":
            for ws in list( self._browser_clients ):
                asyncio.get_running_loop().create_task( ws.close())
            return
        if message.get( "sessionId" ) in self._page_sessions:
            return
        for ws in list( self._browser_clients ):
            asyncio.get_running_loop().create_task( self._send( ws, message ))

    @staticmethod
    async def _send( ws: web.WebSocketResponse, message: dict[ str, Any ]) -> None:
        if not ws.closed:
            try:
                await ws.send_str( json.dumps( message ))
            except ( ConnectionError, RuntimeError ):
                pass

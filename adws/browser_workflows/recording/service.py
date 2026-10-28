"""The recorder service: HTTP routes and the extension's WebSocket, and nothing
else. Every decision is the controller's; every refusal is the guard's.

  GET  /health               {"ok": true}
  GET  /ws                   the extension (Origin-checked)
  GET  /recording/status
  POST /recording/start | /stop | /save {name, description} | /discard
  POST /recording/capture    {event}: inject one raw event (the capture ADWs)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from aiohttp import WSMsgType, web

from ..errors import BrowserWorkflowError, DuplicateName, ExtensionNotConnected, IllegalTransition
from ..interfaces.recording import IRequestGuard
from ..models import ControllerStatus, RawBrowserEvent
from ..structured_log import IStructuredLog
from .controller import RecordingController
from .extension_link import WebSocketExtensionLink

DEFAULT_PORT = 8781
HEARTBEAT_SECONDS = 20.0

_background: set[ asyncio.Task ] = set()

Handler = Callable[[ web.Request ], Awaitable[ web.StreamResponse ]]


def build_recorder_app( controller: RecordingController, link: WebSocketExtensionLink, guard: IRequestGuard,
                        log: IStructuredLog ) -> web.Application:
    app = web.Application( middlewares=[ _guard_middleware( guard, log )])

    async def health( request: web.Request ) -> web.Response:
        return web.json_response({ "ok": True, "service": "browser-recorder" })

    async def status( request: web.Request ) -> web.Response:
        return _status( controller.status())

    async def start( request: web.Request ) -> web.Response:
        return await _run( log, "start", controller.start())

    async def stop( request: web.Request ) -> web.Response:
        return await _run( log, "stop", controller.stop())

    async def save( request: web.Request ) -> web.Response:
        body = await _body( request )
        return await _run( log, "save", controller.save( str( body.get( "name", "" )), str( body.get( "description", "" ))))

    async def discard( request: web.Request ) -> web.Response:
        return await _run( log, "discard", controller.discard())

    async def capture( request: web.Request ) -> web.Response:
        body  = await _body( request )
        info  = controller.status().session
        event = RawBrowserEvent.model_validate( body.get( "event", {}))
        taken = bool( info ) and controller.capture( info.id, event )
        log.write( "capture_injected", kind=event.kind, accepted=taken )
        return _status( controller.status( "captured" if taken else "not recording; event ignored" ), 200 if taken else 409 )

    async def socket( request: web.Request ) -> web.WebSocketResponse:
        ws = web.WebSocketResponse( heartbeat=HEARTBEAT_SECONDS, max_msg_size=1 << 20 )
        await ws.prepare( request )
        link.attach( ws )
        log.write( "extension_connected" )
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    await _on_extension_message( json.loads( msg.data ), controller, link, log )
                except Exception as exc:                      # one bad message must not drop the link
                    log.write( "extension_message_failed", error=str( exc )[ :200 ])
                    await ws.send_json({ "type": "error", "message": str( exc )[ :200 ]})
        finally:
            link.detach( ws )
            log.write( "extension_disconnected" )
        return ws

    app.router.add_get( "/health", health )
    app.router.add_get( "/ws", socket )
    app.router.add_get( "/recording/status", status )
    app.router.add_post( "/recording/start", start )
    app.router.add_post( "/recording/stop", stop )
    app.router.add_post( "/recording/save", save )
    app.router.add_post( "/recording/discard", discard )
    app.router.add_post( "/recording/capture", capture )
    return app


async def _on_extension_message( message: dict[ str, Any ], controller: RecordingController,
                                 link: WebSocketExtensionLink, log: IStructuredLog ) -> None:
    kind = message.get( "type" )
    if kind == "ping":
        await link.notify({ "type": "pong" })
        return
    if link.deliver( message ):
        log.write( "extension_reply", type=kind )
        return
    if kind == "hello":
        log.write( "extension_hello", version=message.get( "extension_version" ), state=message.get( "state" ))
        await controller.extension_said_hello( str( message.get( "state", "idle" )), message.get( "session_id" ))
    elif kind == "event":
        event = RawBrowserEvent.model_validate( message.get( "event", {}))
        taken = controller.capture( str( message.get( "session_id", "" )), event )
        log.write( "event", kind=event.kind, session_id=message.get( "session_id" ), accepted=taken,
                   sensitive=event.sensitive )
    elif kind == "start_requested":
        # start() waits for the extension's reply, which arrives on this same
        # receive loop -- so it must not be awaited here.
        _background.add( task := asyncio.create_task( _quietly( log, "start_requested", controller.start())))
        task.add_done_callback( _background.discard )
    elif kind == "stop_requested":
        await _quietly( log, "stop_requested", controller.stop( from_extension=True ))
    elif kind == "save_requested":
        await _quietly( log, "save_requested", controller.save( str( message.get( "name", "" ))))
    elif kind == "discard_requested":
        await _quietly( log, "discard_requested", controller.discard())
    else:
        log.write( "extension_unknown_message", type=kind )


async def _quietly( log: IStructuredLog, what: str, call: Awaitable[ ControllerStatus ]) -> None:
    try:
        result = await call
        log.write( what, state=result.state, message=result.message )
    except BrowserWorkflowError as exc:
        log.write( what, refused=str( exc ))


async def _run( log: IStructuredLog, what: str, call: Awaitable[ ControllerStatus ]) -> web.Response:
    try:
        result = await call
    except ( IllegalTransition, DuplicateName ) as exc:
        log.write( what, refused=str( exc ))
        return web.json_response({ "error": str( exc )}, status=409 )
    except ExtensionNotConnected as exc:
        log.write( what, refused=str( exc ))
        return web.json_response({ "error": str( exc )}, status=503 )
    except BrowserWorkflowError as exc:
        log.write( what, refused=str( exc ))
        return web.json_response({ "error": str( exc )}, status=422 )
    log.write( what, state=result.state, message=result.message )
    return _status( result )


def _status( status: ControllerStatus, code: int = 200 ) -> web.Response:
    return web.json_response( status.model_dump(), status=code )


async def _body( request: web.Request ) -> dict[ str, Any ]:
    try:
        body = await request.json()
    except ( json.JSONDecodeError, ValueError ):
        raise web.HTTPBadRequest( text='{"error": "body is not JSON"}', content_type="application/json" )
    if not isinstance( body, dict ):
        raise web.HTTPBadRequest( text='{"error": "body must be a JSON object"}', content_type="application/json" )
    return body


def _guard_middleware( guard: IRequestGuard, log: IStructuredLog ):
    @web.middleware
    async def middleware( request: web.Request, handler: Handler ) -> web.StreamResponse:
        host   = request.headers.get( "Host", "" )
        origin = request.headers.get( "Origin" )
        if request.path == "/ws":
            problem = guard.extension_problem( host, origin )
        else:
            problem = guard.control_problem( host, origin, request.headers.get( "Content-Type", "" ),
                                             request.method == "POST" )
        if problem:
            log.write( "refused", path=request.path, host=host, origin=origin, reason=problem )
            return web.json_response({ "error": problem }, status=403 )
        return await handler( request )
    return middleware

"""Cashy's loopback HTTP sidecar: the same routes receptionist_team's
service.py serves (GET /health, GET /alerts, POST /turn, POST /compact,
POST /alerts/ack), so the dashboard reaches her through the bridge it
already uses for Toyota and Sydney.

**It binds 127.0.0.1, and that is not configurable.** She drives a browser
signed in to the person's bank. The dashboard is Tailscale-served; it is the
only thing that may reach her, over loopback.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pydantic import ValidationError

from receptionist_team.service_models import MAX_TURN_CHARS, AlertAckRequest, TurnRequest

from ..interfaces.desk import ICashyDesk

LOOPBACK     = "127.0.0.1"
DEFAULT_PORT = 8770                     # 8767 Toyota, 8768 Mazda, 8769 Sydney


class CashySidecarHandler( BaseHTTPRequestHandler ):
    server_version   = "Cashy/1.0"
    protocol_version = "HTTP/1.0"

    def do_GET( self ) -> None:
        route = self._route()
        if route == "/health":
            self._json( 200, self.server.desk.health())
        elif route == "/alerts":
            self._json( 200, self.server.desk.alerts())
        else:
            self._json( 404, { "ok": False, "error": "not found" })

    def do_POST( self ) -> None:
        route = self._route()
        if route == "/compact":
            self._json( 200, self.server.desk.compact())
            return
        if route == "/alerts/ack":
            if self._read( AlertAckRequest, 64 * 1024 ) is not None:
                self._json( 200, { "ok": True, "acknowledged": 0 })
            return
        if route != "/turn":
            self._json( 404, { "ok": False, "error": "not found" })
            return
        request = self._read( TurnRequest, MAX_TURN_CHARS * 4 )
        if request is None:
            return
        records = self.server.desk.turn( request.text )
        self.send_response( 200 )
        self.send_header( "Content-Type", "application/x-ndjson" )
        self.send_header( "Cache-Control", "no-store" )
        self.send_header( "Connection", "close" )
        self.end_headers()
        self.close_connection = True
        try:
            for record in records:
                self.wfile.write( record.model_dump_json().encode( "utf-8" ) + b"\n" )
            self.wfile.flush()
        except ( BrokenPipeError, ConnectionResetError ):
            pass

    def _read( self, model, max_bytes: int ):
        try:
            length = int( self.headers.get( "Content-Length" ) or 0 )
        except ValueError:
            length = -1
        if length <= 0 or length > max_bytes:
            self._json( 400, { "ok": False, "error": "missing or oversized body" })
            return None
        try:
            return model.model_validate_json( self.rfile.read( length ))
        except ValidationError as exc:
            self._json( 400, { "ok": False, "error": f"invalid request: { exc }" })
            return None

    def _route( self ) -> str:
        return self.path.split( "?", 1 )[ 0 ].rstrip( "/" ) or "/"

    def _json( self, code: int, payload: dict[ str, Any ]) -> None:
        body = json.dumps( payload ).encode( "utf-8" )
        self.send_response( code )
        self.send_header( "Content-Type", "application/json" )
        self.send_header( "Content-Length", str( len( body )))
        self.send_header( "Cache-Control", "no-store" )
        self.end_headers()
        try:
            self.wfile.write( body )
        except ( BrokenPipeError, ConnectionResetError ):
            pass

    def log_message( self, fmt: str, *args ) -> None:
        print( f"[cashy-service] { fmt % args }", flush=True )


class CashySidecar( ThreadingHTTPServer ):
    daemon_threads = True

    def __init__( self, desk: ICashyDesk, port: int = DEFAULT_PORT ) -> None:
        self.desk = desk
        super().__init__(( LOOPBACK, port ), CashySidecarHandler )

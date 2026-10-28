"""IRequestGuard: who may talk to the recorder service.

The service listens on loopback, but loopback is not enough: any web page the
person visits can make their browser send requests to 127.0.0.1. These rules
are what stop that.
"""
from __future__ import annotations

LOOPBACK_NAMES = ( "127.0.0.1", "localhost" )


class LoopbackRequestGuard:
    def __init__( self, port: int, extension_origins: set[ str ]) -> None:
        self._hosts   = { f"{ name }:{ port }" for name in LOOPBACK_NAMES }
        self._origins = set( extension_origins )

    def control_problem( self, host: str, origin: str | None, content_type: str, has_body: bool ) -> str | None:
        """For the CLI/ADW control API: loopback Host, no Origin, JSON bodies."""
        problem = self._host_problem( host )
        if problem:
            return problem
        if origin:
            return "the control API does not take requests from web pages or extensions"
        if has_body and content_type.split( ";" )[ 0 ].strip().lower() != "application/json":
            return "the control API only accepts application/json"
        return None

    def extension_problem( self, host: str, origin: str | None ) -> str | None:
        """For the WebSocket: loopback Host and our extension's exact Origin."""
        problem = self._host_problem( host )
        if problem:
            return problem
        if origin not in self._origins:
            return f"origin { origin or '(none)' } is not the Browser Recorder extension"
        return None

    def _host_problem( self, host: str ) -> str | None:
        # A DNS-rebound name resolves to 127.0.0.1 but still says its own name here.
        if host.lower() not in self._hosts:
            return f"host '{ host }' is not this machine's loopback address"
        return None

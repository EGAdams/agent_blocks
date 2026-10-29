"""StdlibWebSocket: the smallest RFC 6455 client CDP needs, on the stdlib.

Text frames out (masked, as a client must), text frames in (reassembled
from continuations), pings answered, close honoured. No Origin header is
sent, so Chrome accepts the connection without --remote-allow-origins.
"""
from __future__ import annotations

import base64
import os
import socket
import struct
from urllib.parse import urlparse

from browser_workflows.errors import BrowserWorkflowError

_TEXT, _CONT, _CLOSE, _PING, _PONG = 0x1, 0x0, 0x8, 0x9, 0xA


class StdlibWebSocket:
    def __init__( self, url: str, timeout_seconds: float = 10.0 ) -> None:
        parts = urlparse( url )
        if parts.scheme != "ws":
            raise BrowserWorkflowError( f"only plain ws:// to a local Chrome is supported, not { url }" )
        self._sock = socket.create_connection(( parts.hostname, parts.port or 80 ), timeout=timeout_seconds )
        self._buffer = b""
        key = base64.b64encode( os.urandom( 16 )).decode()
        path = parts.path + ( "?" + parts.query if parts.query else "" )
        self._sock.sendall(( f"GET { path } HTTP/1.1\r\nHost: { parts.hostname }:{ parts.port }\r\n"
                             f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: { key }\r\n"
                             f"Sec-WebSocket-Version: 13\r\n\r\n" ).encode())
        head = self._read_until( b"\r\n\r\n" ).decode( "latin-1" )
        if " 101 " not in head.split( "\r\n", 1 )[ 0 ]:
            raise BrowserWorkflowError( f"Chrome refused the DevTools websocket: { head.splitlines()[ 0 ] }" )

    def settimeout( self, seconds: float ) -> None:
        self._sock.settimeout( seconds )

    def send_text( self, text: str ) -> None:
        payload = text.encode( "utf-8" )
        header  = bytearray([ 0x80 | _TEXT ])
        n = len( payload )
        if n < 126:
            header.append( 0x80 | n )
        elif n < 1 << 16:
            header.append( 0x80 | 126 )
            header += struct.pack( "!H", n )
        else:
            header.append( 0x80 | 127 )
            header += struct.pack( "!Q", n )
        mask = os.urandom( 4 )
        masked = bytes( b ^ mask[ i % 4 ] for i, b in enumerate( payload ))
        self._sock.sendall( bytes( header ) + mask + masked )

    def recv_text( self ) -> str:
        parts: list[ bytes ] = []
        while True:
            fin, opcode, payload = self._frame()
            if opcode == _PING:
                self._control( _PONG, payload )
                continue
            if opcode == _PONG:
                continue
            if opcode == _CLOSE:
                raise BrowserWorkflowError( "Chrome closed the DevTools connection" )
            parts.append( payload )
            if fin:
                return b"".join( parts ).decode( "utf-8" )

    def close( self ) -> None:
        try:
            self._control( _CLOSE, b"" )
        except OSError:
            pass
        finally:
            self._sock.close()

    # -- framing -----------------------------------------------------------------

    def _frame( self ) -> tuple[ bool, int, bytes ]:
        first, second = self._read( 2 )
        fin, opcode, length = bool( first & 0x80 ), first & 0x0F, second & 0x7F
        if length == 126:
            length = struct.unpack( "!H", self._read( 2 ))[ 0 ]
        elif length == 127:
            length = struct.unpack( "!Q", self._read( 8 ))[ 0 ]
        mask = self._read( 4 ) if second & 0x80 else b""
        payload = self._read( length )
        if mask:
            payload = bytes( b ^ mask[ i % 4 ] for i, b in enumerate( payload ))
        return fin, opcode, payload

    def _control( self, opcode: int, payload: bytes ) -> None:
        mask = os.urandom( 4 )
        self._sock.sendall( bytes([ 0x80 | opcode, 0x80 | len( payload )]) + mask +
                            bytes( b ^ mask[ i % 4 ] for i, b in enumerate( payload )))

    def _read( self, n: int ) -> bytes:
        while len( self._buffer ) < n:
            chunk = self._sock.recv( max( 65536, n - len( self._buffer )))
            if not chunk:
                raise BrowserWorkflowError( "the DevTools connection dropped" )
            self._buffer += chunk
        data, self._buffer = self._buffer[ :n ], self._buffer[ n: ]
        return data

    def _read_until( self, marker: bytes ) -> bytes:
        while marker not in self._buffer:
            chunk = self._sock.recv( 4096 )
            if not chunk:
                raise BrowserWorkflowError( "the DevTools handshake dropped" )
            self._buffer += chunk
        head, _, self._buffer = self._buffer.partition( marker )
        return head

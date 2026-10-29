"""Offline: the loopback guard, the tab choosers, the websocket framing."""
from __future__ import annotations

import socket
import threading

import pytest

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.choosers import FirstPageChooser, PreferredTargetChooser
from cdp_browser.interfaces import CdpTarget
from cdp_browser.targets import HttpTargetDirectory, LoopbackOnly, pinned
from cdp_browser.websocket import StdlibWebSocket


def test_loopback_guard_refuses_a_remote_debug_port() -> None:
    with pytest.raises( BrowserWorkflowError, match="off this machine" ):
        HttpTargetDirectory( LoopbackOnly()).pages( DebugEndpoint( host="10.0.0.5", port=9222 ))


def test_websocket_url_is_pinned_to_the_endpoint_asked_on() -> None:
    url = pinned( "ws://evil.example:1/devtools/page/ABC", DebugEndpoint( port=9333 ))
    assert url == "ws://127.0.0.1:9333/devtools/page/ABC"


def test_preferred_chooser_follows_session_then_host_then_first() -> None:
    pages = [ CdpTarget( id="a", url="https://news.example/" ),
              CdpTarget( id="b", url="https://secure.chase.com/x" ),
              CdpTarget( id="c", url="https://www.53.com/" )]
    assert PreferredTargetChooser( lambda: ( "c", [ "chase.com" ])).choose( pages ).id == "c"
    assert PreferredTargetChooser( lambda: ( "gone", [ "chase.com" ])).choose( pages ).id == "b"
    assert PreferredTargetChooser( lambda: ( "", [])).choose( pages ).id == "a"
    assert FirstPageChooser().choose([]) is None


def _server( frames: list[ bytes ]) -> int:
    listener = socket.socket()
    listener.bind(( "127.0.0.1", 0 ))
    listener.listen( 1 )

    def serve() -> None:
        conn, _ = listener.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv( 4096 )
        conn.sendall( b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n" )
        for frame in frames:
            conn.sendall( frame )
        conn.recv( 4096 )
        conn.close()
        listener.close()
    threading.Thread( target=serve, daemon=True ).start()
    return listener.getsockname()[ 1 ]


def test_websocket_reassembles_continuations_and_long_frames() -> None:
    long_text = b"x" * 70000
    frames = [ bytes([ 0x01, 3 ]) + b"hel", bytes([ 0x80, 2 ]) + b"lo",            # fragmented "hello"
               bytes([ 0x89, 0 ]),                                                  # a ping in between
               bytes([ 0x81, 127 ]) + len( long_text ).to_bytes( 8, "big" ) + long_text ]
    ws = StdlibWebSocket( f"ws://127.0.0.1:{ _server( frames )}/devtools/page/1" )
    assert ws.recv_text() == "hello"
    assert ws.recv_text() == "x" * 70000
    ws.close()

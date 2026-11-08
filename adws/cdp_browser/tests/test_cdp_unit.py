"""Offline: the loopback guard, the tab choosers, the websocket framing."""
from __future__ import annotations

import socket
import threading

import pytest

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.element import CdpElement
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


class FakeChannel:
    """A page that answers CDP calls. `holds` is the field's value; `takes_keys` False is a field
    that ignores typed input the way a date input ignores "31/12/2025" -- it stays empty."""

    def __init__( self, takes_keys: bool = True, shapes = None ) -> None:
        self.holds, self.takes_keys, self.shapes, self.typed = "", takes_keys, shapes, []

    def call( self, method: str, params: dict ) -> dict:
        if method == "Input.insertText":
            self.typed.append( params[ "text" ])
            if self.takes_keys:
                self.holds = self.shapes( params[ "text" ]) if self.shapes else params[ "text" ]
            return {}
        source = params[ "functionDeclaration" ]
        given = [ a[ "value" ] for a in params.get( "arguments", [])]
        if "this.focus()" in source and "this.value = ''" in source:          # _EMPTY
            self.holds = ""
            return { "result": { "value": True }}
        if "Object.getOwnPropertyDescriptor" in source:                        # _ASSIGN
            self.holds = self.shapes( given[ 0 ]) if self.shapes else given[ 0 ]
            return { "result": { "value": self.holds }}
        return { "result": { "value": self.holds }}                            # _SETTLE and reads


def test_a_field_that_ignores_typed_keys_is_told_its_value_outright() -> None:
    """A date input takes no keys: filling it used to leave it empty and report a mismatch."""
    channel = FakeChannel( takes_keys=False )
    assert CdpElement( channel, "node-1" ).fill( "2025-12-31" ) == "2025-12-31"
    assert channel.typed == [ "2025-12-31" ]                                   # typing is still tried first


def test_a_field_that_reformats_what_is_typed_keeps_its_own_version() -> None:
    """The mask is the field doing its job, so the typed result wins over a blunt assignment."""
    channel = FakeChannel( shapes=lambda t: "(616) 555-1212" if t == "6165551212" else t )
    assert CdpElement( channel, "node-1" ).fill( "6165551212" ) == "(616) 555-1212"


def test_a_filled_field_is_emptied_first_so_filling_replaces() -> None:
    channel = FakeChannel()
    channel.holds = "old"
    assert CdpElement( channel, "node-1" ).fill( "new" ) == "new"

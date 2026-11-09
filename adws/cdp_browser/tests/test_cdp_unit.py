"""Offline: the loopback guard, the tab choosers, the websocket framing."""
from __future__ import annotations

import socket
import threading

import pytest

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.browser import CdpWorkflowBrowser
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


class ClickChannel:
    """A page answering `click`'s calls: `box` is what _BOX measures; `sent` records what was done."""

    def __init__( self, box: dict ) -> None:
        self.box, self.sent = box, []

    def call( self, method: str, params: dict ) -> dict:
        if method == "Input.dispatchMouseEvent":
            self.sent.append( params[ "type" ])
            return {}
        source = params[ "functionDeclaration" ]
        if "elementFromPoint" in source:
            return { "result": { "value": self.box }}
        if "this.click()" in source:
            self.sent.append( "this.click" )
        return { "result": { "value": "a" }}


def test_a_click_on_a_page_chrome_is_drawing_is_a_real_mouse_press() -> None:
    channel = ClickChannel({ "x": 5, "y": 5, "width": 10, "height": 10, "clear": True, "drawn": True })
    CdpElement( channel, "node-1" ).click()
    assert channel.sent == [ "mousePressed", "mouseReleased" ]


def test_a_click_on_a_hidden_page_is_made_in_the_page_because_a_mouse_press_is_dropped() -> None:
    """Accela's Search link, live: dispatched mouse events on a hidden tab did nothing at all."""
    channel = ClickChannel({ "x": 5, "y": 5, "width": 10, "height": 10, "clear": True, "drawn": False })
    CdpElement( channel, "node-1" ).click()
    assert channel.sent == [ "this.click" ]


class RecordingChannel:
    """Records the one Runtime.evaluate `find`/`body_text` sends, and answers with `answer`."""

    def __init__( self, answer: dict ) -> None:
        self.answer, self.expression, self.calls = answer, "", []

    def call( self, method: str, params: dict, **_: object ) -> dict:
        self.calls.append( method )
        if method == "Runtime.evaluate":
            self.expression = params[ "expression" ]
            return self.answer
        if method == "Runtime.getProperties":
            return { "result": []}
        return {}


def _browser( channel: RecordingChannel ) -> CdpWorkflowBrowser:
    """A CdpWorkflowBrowser whose page channel is the recorder, with no socket opened."""
    page = CdpWorkflowBrowser.__new__( CdpWorkflowBrowser )
    page._page = channel                                                       # noqa: SLF001
    return page


def test_find_searches_frames_as_well_as_the_page() -> None:
    """Accela's `.tif` link lives in `iframeAttachmentList`; a search of `document` alone said "none"
    for an element that was plainly on the screen."""
    channel = RecordingChannel({ "result": {}})
    _browser( channel ).find( 'a[id*="lnkFileName"]' )
    assert "contentDocument" in channel.expression                             # it descends into frames
    assert '"a[id*=\\"lnkFileName\\"]"' in channel.expression                   # the selector is passed as data
    assert "depth >= 5" in channel.expression                                  # and cannot recurse forever


def test_find_still_finds_nothing_for_a_selector_chrome_rejects() -> None:
    channel = RecordingChannel({ "exceptionDetails": { "text": "invalid selector" }})
    assert _browser( channel ).find( "a[[[bad" ) == []


def test_find_finds_nothing_when_the_page_answers_without_an_object() -> None:
    assert _browser( RecordingChannel({ "result": {}})).find( "a" ) == []


def test_body_text_reads_frames_too_and_says_which_frame() -> None:
    """`browser.read` showed none of the attachment list, so a caller had to guess its selectors."""
    channel = RecordingChannel({ "result": { "value": "page text" }})
    assert _browser( channel ).body_text() == "page text"
    assert "contentDocument" in channel.expression
    assert "[frame " in channel.expression                                     # each frame's text is labelled


def test_a_command_chrome_never_answers_says_so_rather_than_failing_to_format_the_message():
    """2026-11-09: `{ timeout_seconds:.0f }` (bracket spacing inside a format spec) raised ValueError, so the
    real cause -- Chrome did not answer -- never reached the caller."""
    from cdp_browser.channel import WebSocketCdpChannel

    class Silent:
        def send_text( self, text ): pass
        def settimeout( self, seconds ): pass
        def recv_text( self ): raise socket.timeout()

    with pytest.raises( BrowserWorkflowError, match="did not answer Page.reload within 0s" ):
        WebSocketCdpChannel( Silent()).call( "Page.reload", timeout_seconds=0.01 )


class StuckLoadingPage:
    """A tab whose reload parses the page but never finishes its embedded pages (readyState stays
    "interactive"), the way the dashboard behaves in a hidden window while its iframes load."""

    def __init__( self ) -> None:
        self.marked = True

    def call( self, method: str, params: dict | None = None, timeout_seconds: float = 30.0 ) -> dict:
        if method == "Page.reload":
            self.marked = False                                                # a new document
            return {}
        expression = ( params or {}).get( "expression", "" )
        if expression.startswith( "window." ):
            self.marked = True
            return { "result": { "value": True }}
        if expression.startswith( "!!window." ):
            return { "result": { "value": self.marked }}
        if expression == "document.readyState":
            return { "result": { "value": "interactive" }}
        return {}

    def close( self ) -> None:
        pass


def _stuck_browser( timeout: float ) -> CdpWorkflowBrowser:
    class Connector:
        def connect( self, url: str ) -> StuckLoadingPage:
            return StuckLoadingPage()
    return CdpWorkflowBrowser( DebugEndpoint( port=9222 ),
                               CdpTarget( id="t", url="https://x.example/", webSocketDebuggerUrl="ws://127.0.0.1:9222/devtools/page/t" ),
                               directory=None, connector=Connector(), load_timeout_seconds=timeout )


def test_a_reload_can_wait_for_the_document_without_its_embedded_pages() -> None:
    _stuck_browser( timeout=5.0 ).reload( ready="interactive" )             # returns: the document is there
    with pytest.raises( BrowserWorkflowError, match="did not finish loading" ):
        _stuck_browser( timeout=0.5 ).reload()                                 # "complete" still means complete

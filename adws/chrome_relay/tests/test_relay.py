"""The relay against a fake everyday Chrome: one upstream connection however many clients, sessions kept apart,
ids restored, loopback guard, and 503 when Chrome isn't there. Offline; plain asyncio.run, no plugin."""
import asyncio
import json
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from chrome_relay.locate import ChromeNotReachable, PortFileLocator   # noqa: E402
from chrome_relay.server import Relay                                  # noqa: E402
from chrome_relay.upstream import UpstreamBrowser                      # noqa: E402


class FakeChrome:
    """A browser websocket only, like Chrome with chrome://inspect/#remote-debugging on."""

    def __init__( self ) -> None:
        self.connections = 0
        self.methods: list[ str ] = []
        self.targets = [{ "targetId": "T1", "type": "page", "url": "https://example.test/", "title": "Example" }]
        self.sessions: dict[ str, str ] = {}

    async def socket( self, request: web.Request ) -> web.WebSocketResponse:
        self.connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare( request )
        async for frame in ws:
            message = json.loads( frame.data )
            method, params, session = message[ "method" ], message.get( "params", {}), message.get( "sessionId" )
            self.methods.append( method )
            reply: dict = { "id": message[ "id" ]}
            if method == "Target.getTargets":
                reply[ "result" ] = { "targetInfos": self.targets }
            elif method == "Target.createTarget":
                new = f"T{ len( self.targets ) + 1 }"
                self.targets.append({ "targetId": new, "type": "page", "url": params[ "url" ], "title": "" })
                reply[ "result" ] = { "targetId": new }
            elif method == "Target.attachToTarget":
                if params[ "targetId" ] not in { t[ "targetId" ] for t in self.targets }:
                    reply[ "error" ] = { "code": -32602, "message": "No target with given id found" }
                else:
                    sid = f"S{ len( self.sessions ) + 1 }"
                    self.sessions[ sid ] = params[ "targetId" ]
                    reply[ "result" ] = { "sessionId": sid }
            elif method == "Runtime.evaluate" and session:
                reply[ "result" ] = { "result": { "value": f"{ self.sessions[ session ] }:{ params[ 'expression' ] }" }}
                reply[ "sessionId" ] = session
                await ws.send_str( json.dumps({ "method": "Page.loadEventFired", "params": {}, "sessionId": session }))
            elif method == "Browser.getVersion":
                reply[ "result" ] = { "product": "Chrome/158" }
            else:
                reply[ "result" ] = {}
            await ws.send_str( json.dumps( reply ))
        return ws


async def _serve( app: web.Application ) -> tuple[ web.AppRunner, int ]:
    runner = web.AppRunner( app )
    await runner.setup()
    site = web.TCPSite( runner, "127.0.0.1", 0 )
    await site.start()
    return runner, site._server.sockets[ 0 ].getsockname()[ 1 ]


async def _with_relay( body, chrome_up: bool = True ) -> None:
    chrome = FakeChrome()
    chrome_app = web.Application()
    chrome_app.router.add_get( "/devtools/browser/{key}", chrome.socket )
    chrome_runner, chrome_port = await _serve( chrome_app )
    url = f"ws://127.0.0.1:{ chrome_port }/devtools/browser/secret-key"

    def locate() -> str:
        if not chrome_up:
            raise ChromeNotReachable( "Chrome isn't open" )
        return url

    upstream = UpstreamBrowser( locate, connect_seconds=5 )
    relay_runner, relay_port = await _serve( Relay( upstream ).app())
    try:
        async with aiohttp.ClientSession() as http:
            await body( http, f"http://127.0.0.1:{ relay_port }", chrome )
    finally:
        await upstream.close()
        await relay_runner.cleanup()
        await chrome_runner.cleanup()


def test_version_answers_without_touching_chrome() -> None:
    async def body( http, base, chrome ) -> None:
        async with http.get( f"{ base }/json/version" ) as r:
            assert r.status == 200
            data = await r.json()
        assert data[ "webSocketDebuggerUrl" ].endswith( "/devtools/browser/relay" )
        assert chrome.connections == 0
    asyncio.run( _with_relay( body ))


def test_list_new_and_page_sessions_share_one_upstream_connection() -> None:
    async def body( http, base, chrome ) -> None:
        async with http.get( f"{ base }/json/list" ) as r:
            tabs = await r.json()
        assert tabs[ 0 ][ "id" ] == "T1" and tabs[ 0 ][ "webSocketDebuggerUrl" ] == f"{ base.replace( 'http', 'ws' ) }/devtools/page/T1"
        async with http.put( f"{ base }/json/new?https://bank.test/login?x=1" ) as r:
            new = await r.json()
        assert new[ "id" ] == "T2" and new[ "url" ] == "https://bank.test/login?x=1"
        a = await http.ws_connect( tabs[ 0 ][ "webSocketDebuggerUrl" ])
        b = await http.ws_connect( new[ "webSocketDebuggerUrl" ])
        await a.send_json({ "id": 7, "method": "Runtime.evaluate", "params": { "expression": "1" }})
        await b.send_json({ "id": 7, "method": "Runtime.evaluate", "params": { "expression": "2" }})
        got_a = [ await a.receive_json( timeout=5 ), await a.receive_json( timeout=5 )]
        got_b = [ await b.receive_json( timeout=5 ), await b.receive_json( timeout=5 )]
        reply_a = next( m for m in got_a if "id" in m )
        reply_b = next( m for m in got_b if "id" in m )
        assert reply_a == { "id": 7, "result": { "result": { "value": "T1:1" }}}
        assert reply_b == { "id": 7, "result": { "result": { "value": "T2:2" }}}
        assert { "method": "Page.loadEventFired", "params": {}} in got_a
        assert all( "sessionId" not in m for m in got_a + got_b )
        browser = await http.ws_connect( f"{ base }/devtools/browser/relay" )
        await browser.send_json({ "id": 1, "method": "Browser.getVersion" })
        assert ( await browser.receive_json( timeout=5 )) == { "id": 1, "result": { "product": "Chrome/158" }}
        for ws in ( a, b, browser ):
            await ws.close()
        await asyncio.sleep( 0.1 )
        assert chrome.connections == 1
        assert chrome.methods.count( "Target.detachFromTarget" ) == 2
        assert "Browser.close" not in chrome.methods
    asyncio.run( _with_relay( body ))


def test_unknown_tab_is_404() -> None:
    async def body( http, base, chrome ) -> None:
        async with http.get( f"{ base }/devtools/page/NOPE" ) as r:
            assert r.status == 404
    asyncio.run( _with_relay( body ))


def test_web_pages_and_rebound_hosts_are_refused() -> None:
    async def body( http, base, chrome ) -> None:
        async with http.get( f"{ base }/json/list", headers={ "Origin": "https://evil.test" }) as r:
            assert r.status == 403
        async with http.get( f"{ base }/json/list", headers={ "Host": "evil.test:9223" }) as r:
            assert r.status == 403
        assert chrome.connections == 0
    asyncio.run( _with_relay( body ))


def test_chrome_not_open_is_503_with_a_reason() -> None:
    async def body( http, base, chrome ) -> None:
        async with http.get( f"{ base }/json/list" ) as r:
            assert r.status == 503
            assert "Chrome isn't open" in r.reason
    asyncio.run( _with_relay( body, chrome_up=False ))


def test_port_file_locator( tmp_path: Path ) -> None:
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text( "51643\n/devtools/browser/abc-123\n" )
    assert PortFileLocator( port_file )() == "ws://127.0.0.1:51643/devtools/browser/abc-123"
    port_file.write_text( "garbage" )
    try:
        PortFileLocator( port_file )()
        raise AssertionError( "expected ChromeNotReachable" )
    except ChromeNotReachable as exc:
        assert "abc" not in str( exc )
    try:
        PortFileLocator( tmp_path / "missing" )()
        raise AssertionError( "expected ChromeNotReachable" )
    except ChromeNotReachable:
        pass


def test_a_slow_allow_is_a_quick_503_and_the_next_request_succeeds() -> None:
    async def scenario() -> None:
        allowed = asyncio.Event()
        chrome = FakeChrome()

        async def gated( request: web.Request ) -> web.WebSocketResponse:
            await allowed.wait()                                   # Chrome holds the upgrade until Allow
            return await chrome.socket( request )

        chrome_app = web.Application()
        chrome_app.router.add_get( "/devtools/browser/{key}", gated )
        chrome_runner, chrome_port = await _serve( chrome_app )
        upstream = UpstreamBrowser( lambda: f"ws://127.0.0.1:{ chrome_port }/devtools/browser/k", answer_seconds=0.3 )
        relay_runner, relay_port = await _serve( Relay( upstream ).app())
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get( f"http://127.0.0.1:{ relay_port }/json/list" ) as r:
                    assert r.status == 503 and "click Allow" in r.reason
                allowed.set()
                await asyncio.sleep( 0.3 )
                async with http.get( f"http://127.0.0.1:{ relay_port }/json/list" ) as r:
                    assert r.status == 200
            assert chrome.connections == 1
        finally:
            await upstream.close()
            await relay_runner.cleanup()
            await chrome_runner.cleanup()
    asyncio.run( scenario())

"""A small pretend bank on 127.0.0.1 for Cashy's live tests: a sign-in page
with a password field, a signed-in dashboard, statements by month, and
transfer activity -- plus the buttons Cashy must never press on her own."""
from __future__ import annotations

import http.server
import threading
from typing import Iterator

import pytest

STATEMENT = b"%PDF-1.4 pretend statement "


def _page( title: str, body: str ) -> bytes:
    return f"<html><head><title>{ title }</title></head><body>{ body }</body></html>".encode()


NAV = """<nav><a href="/accounts">Accounts</a> <a href="/documents">Statements &amp; documents</a>
  <a href="/pay">Pay &amp; transfer</a> <a href="/logout">Sign out</a></nav>"""

SIGNED_IN = {
    "/dashboard": _page( "Overview", NAV + """<h1>Account overview</h1>
        <p>Checking ...6285</p><button id="pay-now">Make a payment</button>""" ),
    "/accounts":  _page( "Accounts", NAV + "<h1>Accounts</h1><p>Checking ...6285</p>" ),
    "/documents": _page( "Statements", NAV + """<h1>Statements</h1><ul>
        <li><a href="/statement/2025-08.pdf">August 2025</a></li>
        <li><a href="/statement/2025-09.pdf">September 2025</a></li>
        <li><a href="/statement/2025-10.pdf">October 2025</a></li></ul>""" ),
    "/pay":       _page( "Pay and transfer", NAV + """<h1>Pay &amp; transfer</h1>
        <a href="/pay/activity">Transfer activity</a> <button id="send">Send money</button>""" ),
    "/pay/activity": _page( "Transfer activity", NAV + """<h1>Transfer activity</h1><table>
        <tr><td>2025-09-28</td><td>gas at church</td><td>from ...6285</td><td>$60.57</td></tr>
        <tr><td>2025-09-12</td><td>electric co</td><td>from ...6285</td><td>$88.10</td></tr></table>""" ),
}

LOGIN = _page( "Sign in", """<h1>Sign in</h1><form action="/signin">
    <label for="u">Username</label><input id="u" name="u">
    <label for="p">Password</label><input id="p" name="p" type="password">
    <button type="submit">Sign in</button></form>""" )


class _Handler( http.server.BaseHTTPRequestHandler ):
    def do_GET( self ) -> None:
        path      = self.path.split( "?" )[ 0 ]
        signed_in = "s=1" in ( self.headers.get( "Cookie" ) or "" )
        if path == "/signin":
            return self._redirect( "/dashboard", "s=1; Path=/" )
        if path == "/logout":
            return self._redirect( "/login", "s=0; Path=/" )
        if path.startswith( "/statement/" ) and signed_in:
            self.send_response( 200 )
            self.send_header( "Content-Type", "application/pdf" )
            self.send_header( "Content-Disposition", f'attachment; filename="statement_{ path[ 11:-4 ] }.pdf"' )
            self.end_headers()
            self.wfile.write( STATEMENT + path.encode())
            return None
        if path in SIGNED_IN and not signed_in:
            return self._redirect( "/login" )
        body = SIGNED_IN.get( path ) or LOGIN
        self.send_response( 200 )
        self.send_header( "Content-Type", "text/html" )
        self.end_headers()
        self.wfile.write( body )
        return None

    def _redirect( self, to: str, cookie: str = "" ) -> None:
        self.send_response( 302 )
        self.send_header( "Location", to )
        if cookie:
            self.send_header( "Set-Cookie", cookie )
        self.end_headers()

    def log_message( self, *args: object ) -> None:
        pass


@pytest.fixture
def bank_site() -> Iterator[ str ]:
    server = http.server.ThreadingHTTPServer(( "127.0.0.1", 0 ), _Handler )
    threading.Thread( target=server.serve_forever, daemon=True ).start()
    yield f"http://127.0.0.1:{ server.server_address[ 1 ]}"
    server.shutdown()

"""A small fake bank on 127.0.0.1, for the live tests."""
from __future__ import annotations

import http.server
import threading

import pytest

PAGES = {
    "/": b"""<html><head><title>Test Bank</title></head><body>
      <nav><a href="/statements" id="stm">Statements</a> <a href="/activity">Transfer Activity</a></nav>
      <form action="/search"><label for="q">Find</label><input id="q" name="q">
        <select id="m"><option value="9">September</option><option value="10">October</option></select>
        <button type="submit">Search</button></form>
      <div style="display:none"><a href="/hidden">Hidden</a></div></body></html>""",
    "/statements": b"""<html><head><title>Statements</title></head><body>
      <h1>Your statements</h1><a href="/file.pdf" id="dl">September 2025 statement</a></body></html>""",
    "/file.pdf": b"%PDF-1.4 test",
}


class _Handler( http.server.BaseHTTPRequestHandler ):
    def do_GET( self ) -> None:
        path = self.path.split( "?" )[ 0 ]
        body = PAGES.get( path, b"<html><head><title>Results</title></head><body>found</body></html>" )
        self.send_response( 200 )
        if path.endswith( ".pdf" ):
            self.send_header( "Content-Type", "application/pdf" )
            self.send_header( "Content-Disposition", 'attachment; filename="sept.pdf"' )
        else:
            self.send_header( "Content-Type", "text/html" )
        self.end_headers()
        self.wfile.write( body )

    def log_message( self, *args: object ) -> None:
        pass


@pytest.fixture
def site() -> str:
    server = http.server.ThreadingHTTPServer(( "127.0.0.1", 0 ), _Handler )
    threading.Thread( target=server.serve_forever, daemon=True ).start()
    yield f"http://127.0.0.1:{ server.server_address[ 1 ]}"
    server.shutdown()



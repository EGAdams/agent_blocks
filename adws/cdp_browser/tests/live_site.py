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
    # The dashboard's own deed-review rules (css/dashboard.css), so the highlight probe is tested
    # against the animation the Notes page really runs.
    "/notes": b"""<html><head><title>Notes</title><style>
      .notes-preview .notes-highlight-yellow { background: #fff59d; }
      .notes-preview .notes-deed-review-active-line {
        animation: notes-active-deed-warning 1.4s steps(2, jump-none) infinite; }
      @keyframes notes-active-deed-warning {
        0%, 49% { background-color: #fff59d; } 50%, 100% { background-color: transparent; } }
      .fade { animation: fade 1s infinite; } @keyframes fade { 0% { opacity: 1; } 100% { opacity: 0; } }
      </style></head><body><textarea id="t">saved text</textarea><div id="p" class="notes-preview">
      <p>Plain words.</p>
      <p><mark class="notes-highlight-yellow notes-deed-review-active-line">[VERIFY PLAT RECORD REFERENCE: Liber 4]</mark></p>
      <p><mark class="notes-highlight-yellow notes-deed-review-verify">[VERIFY TITLE EXCEPTIONS: list]</mark></p>
      <p><span class="fade blink-text">gone</span></p></div></body></html>""",
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



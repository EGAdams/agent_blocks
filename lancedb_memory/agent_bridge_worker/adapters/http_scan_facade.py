"""IScanFacade over the dashboard's loopback scan façade, with urllib.

The façade answers only loopback callers holding a token from
`~/.config/scan_bridge/clients.json`; this is the one client that holds such a
token. The token is sent in the Authorization header and never logged, and
the base URL is refused unless it is loopback, so a misconfigured URL can never
send the token, or a scan request, off this machine.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from ..models import ScanPage
from ..ports import GatewayError

LOOPBACK  = re.compile( r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?/" )
SCAN_ID   = re.compile( r"scan_[0-9A-HJKMNP-TV-Z]{26}" )
SCANNERS  = frozenset({ "window", "freezer" })
RENDITION = frozenset({ "preview", "full" })
MAX_READ  = 64 * 1024 * 1024        # well past the largest original the store keeps


def load_scan_token( path: Path ) -> str:
    """The façade token from a 0600 file; a file anyone else can read is refused, not used."""
    path = Path( path )
    if path.stat().st_mode & 0o077:
        raise RuntimeError( f"{ path } must be readable by its owner only (chmod 600)" )
    token = path.read_text( encoding="utf-8" ).strip()
    if len( token ) < 24:
        raise RuntimeError( f"{ path } does not hold a usable token" )
    return token


class HttpScanFacade:
    def __init__( self, base_url: str, token: str, timeout_seconds: float = 30.0 ) -> None:
        if not LOOPBACK.match( base_url.rstrip( "/" ) + "/" ):
            raise ValueError( "the scan façade is reached on loopback only" )
        self._base    = base_url.rstrip( "/" )
        self._token   = token
        self._timeout = timeout_seconds

    def __repr__( self ) -> str:            # never show the token
        return f"HttpScanFacade({ self._base!r })"

    # -- IScanFacade ----------------------------------------------------------

    def start( self, scanner: str, idempotency_key: str ) -> dict:
        if scanner not in SCANNERS:
            raise ValueError( "scanner must be window or freezer" )
        body = json.dumps({ "scanner": scanner, "idempotency_key": idempotency_key }).encode( "utf-8" )
        return self._json( "POST", "/scans", body )

    def status( self, scan_id: str ) -> dict:
        return self._json( "GET", f"/scans/{ self._id( scan_id )}" )

    def document( self, scan_id: str ) -> dict:
        return self._json( "GET", f"/scans/{ self._id( scan_id )}/document" )

    def original( self, scan_id: str ) -> bytes:
        return self._send( "GET", f"/scans/{ self._id( scan_id )}/original" )[ 0 ]

    def page( self, scan_id: str, page: int, rendition: str ) -> ScanPage:
        if rendition not in RENDITION or not 1 <= int( page ) <= 500:
            raise ValueError( "a page is 1..500, preview or full" )
        raw, headers = self._send( "GET", f"/scans/{ self._id( scan_id )}/pages/{ int( page )}?rendition={ rendition }" )
        return ScanPage( jpeg=raw, width=int( headers.get( "X-Image-Width", "0" )), height=int( headers.get( "X-Image-Height", "0" )))

    def text( self, scan_id: str, page: int ) -> dict:
        return self._json( "GET", f"/scans/{ self._id( scan_id )}/text?page={ int( page )}" )

    def recent( self, limit: int ) -> dict:
        """The newest scans (`scanner:list`), so Scala can resolve "the last scan" to a scan id."""
        return self._json( "GET", f"/scans?limit={ max( 1, min( int( limit ), 50 ))}" )

    def mazda( self, scan_id: str ) -> dict:
        return self.outcomes( scan_id )[ "mazda" ]

    def outcomes( self, scan_id: str ) -> dict:
        """Scala's filing and Mazda's verdict, from the one route that knows both (an older façade has no scala)."""
        reply = self._json( "GET", f"/scans/{ self._id( scan_id )}/mazda" )
        return { "scala": dict( reply.get( "scala" ) or {}), "mazda": dict( reply.get( "mazda" ) or {})}

    # -- plumbing -------------------------------------------------------------

    @staticmethod
    def _id( scan_id: str ) -> str:
        if not SCAN_ID.fullmatch( scan_id ):
            raise ValueError( "not a scan id" )
        return scan_id

    def _json( self, method: str, path: str, body: bytes | None = None ) -> dict:
        raw, _ = self._send( method, path, body )
        out = json.loads( raw or b"{}" )
        if not isinstance( out, dict ):
            raise GatewayError( 502, "bad_reply", "the scan façade did not answer with an object" )
        return out

    def _send( self, method: str, path: str, body: bytes | None = None ) -> tuple[ bytes, dict[ str, str ]]:
        request = urllib.request.Request( self._base + path, data=body, method=method )
        request.add_header( "Authorization", f"Bearer { self._token }" )
        if body is not None:
            request.add_header( "Content-Type", "application/json" )
        try:
            response = urllib.request.urlopen( request, timeout=self._timeout )
            try:
                return response.read( MAX_READ ), { k: v for k, v in response.headers.items()}
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            code, message = "http_error", exc.reason or ""
            try:
                err     = json.loads( exc.read() or b"{}" ).get( "error", {})
                code    = str( err.get( "code", code ))
                message = str( err.get( "message", message ))
            except ( ValueError, AttributeError ):
                pass
            raise GatewayError( exc.code, code, message ) from None
        except urllib.error.URLError as exc:
            raise GatewayError( 0, "unreachable", str( exc.reason )) from None

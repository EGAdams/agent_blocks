#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic"]
# ///
"""ADW Notes Render Check -- did the dashboard's Notes page render the version that was saved?

Opens the dashboard Notes page (`http://127.0.0.1:8765/dashboard.html` by
default) in its own headless Chrome -- its own debug port and a throwaway
profile, never the user's window -- clicks the Bridge Notes *diagnostic* note
and reads back what the editor shows. The rendered revision is the sha256 of
the editor's text, computed inside the page, which is how `notes_store`
names a revision (the sha256 of the file's bytes), so it can be compared with
the revision the notes agent wrote.

It records one `browser.render:<adw_id>` object in the object trace:

    inputs   { filename, expected_marker, url }
    outputs  { revision, marker_seen, markers, status, listed }

The object inspector's "did it save, and did the browser render that
version?" reads exactly those fields. Only the diagnostic note is opened
(`bridge-notes-diagnostic*`); a filename outside that stem is refused before
a browser starts, and no note text is logged -- only the NOTESDIAG markers.

    Phases:  LaunchBrowser -> OpenNote -> ReadRender -> Record

    lancedb_memory/.venv/bin/python adws/adw_notes_render_check.py [--filename F] [--marker NOTESDIAG-…]

Exit status 0 when the page rendered (and showed the marker, if one was
given), 1 when it did not, 2 when the check could not run.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( HERE ))
sys.path.insert( 0, str( HERE.parent / "lancedb_memory" ))

from cdp_browser.channel import WebSocketCdpConnector        # noqa: E402
from object_trace.adw_hooks import AdwRunTrace               # noqa: E402
from object_trace.factory import default_tracer              # noqa: E402

DASHBOARD = "http://127.0.0.1:8765/dashboard.html"
STEM      = "bridge-notes-diagnostic"
MARKER    = re.compile( r"^NOTESDIAG-[0-9a-f]{12}$" )
PORT      = 9341

LISTED = """(() => { const l = document.getElementById( 'notes-landing' );
  if ( !l ) return null;
  return [ ...l.querySelectorAll( 'button' )].map( b => b.textContent.trim()).filter( t => t.includes( %s )); })()"""

CLICK = """(() => { const b = [ ...document.querySelectorAll( '#notes-landing button' )].find( b => b.textContent.includes( %s ));
  if ( !b ) return false; b.click(); return true; })()"""

# Hashes the editor text as UTF-8 bytes, the way notes_store names a revision; returns markers only, never the text.
READ = """(async () => { const t = document.getElementById( 'notes-editor-text' ), p = document.getElementById( 'notes-editor-preview' ),
  s = document.getElementById( 'notes-editor-status' );
  const text = t ? t.value : '';
  let revision = null;
  if ( t && text && window.crypto && crypto.subtle ) {
    const d = await crypto.subtle.digest( 'SHA-256', new TextEncoder().encode( text ));
    revision = [ ...new Uint8Array( d )].map( b => b.toString( 16 ).padStart( 2, '0' )).join( '' ); }
  const re = /NOTESDIAG-[0-9a-f]{12}/g;
  return { revision, secure: !!window.isSecureContext, chars: text.length,
           text_markers: text.match( re ) || [], preview_markers: (( p && p.textContent ) || '' ).match( re ) || [],
           status: s ? s.textContent.trim().slice( 0, 160 ) : null }; })()"""


class RenderCheckError( RuntimeError ):
    pass


class NotesRenderCheck:
    """The four phases; each one is a method so the ADW wrapper can trace it."""

    def __init__( self, url: str, filename: str | None, marker: str | None, port: int, wait_seconds: float ) -> None:
        self.url, self.filename, self.marker, self.port, self.wait = url, filename, marker, port, wait_seconds
        self._chrome: subprocess.Popen | None = None
        self._profile: str | None = None
        self._ch: Any = None
        self.render: dict[ str, Any ] = {}
        self.listed: list[ str ] = []

    # -- phases -------------------------------------------------------------------------

    def launch_browser( self ) -> None:
        self._profile = tempfile.mkdtemp( prefix="notes-render-" )
        self._chrome = subprocess.Popen([ "google-chrome", "--headless=new", "--disable-gpu", "--no-first-run",
                                          f"--remote-debugging-port={ self.port }", f"--user-data-dir={ self._profile }",
                                          "about:blank" ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                targets = json.load( urllib.request.urlopen( f"http://127.0.0.1:{ self.port }/json", timeout=2 ))
                page = next( t for t in targets if t[ "type" ] == "page" )
                self._ch = WebSocketCdpConnector().connect( page[ "webSocketDebuggerUrl" ])
                self._ch.call( "Runtime.enable" )
                return
            except Exception:                 # noqa: BLE001 -- not up yet
                time.sleep( 0.3 )
        raise RenderCheckError( f"headless Chrome did not open a debug port on { self.port } within 15 s" )

    def open_note( self ) -> None:
        self._ch.call( "Page.navigate", { "url": f"{ self.url }?_render_check={ int( time.time())}" })
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline:
            time.sleep( 0.5 )
            found = self._eval( LISTED % json.dumps( STEM ))
            if found:
                self.listed = found
                break
        if not self.listed:
            raise RenderCheckError( f"the Notes page lists no { STEM } note (is the dashboard on { self.url }?)" )
        if self.filename is None:
            self.filename = sorted( self.listed )[ -1 ]
        if not any( self.filename in b for b in self.listed ):
            raise RenderCheckError( f"the Notes page does not list { self.filename }" )
        if not self._eval( CLICK % json.dumps( self.filename )):
            raise RenderCheckError( f"could not click { self.filename } on the Notes page" )

    def read_render( self ) -> None:
        deadline = time.monotonic() + self.wait
        while True:
            time.sleep( 0.5 )
            self.render = self._eval( READ ) or {}
            shown = set( self.render.get( "text_markers", [])) | set( self.render.get( "preview_markers", []))
            ready = self.render.get( "revision" ) and ( self.marker is None or self.marker in shown )
            if ready or time.monotonic() > deadline:
                break
        if not self.render.get( "secure" ):
            raise RenderCheckError( "the page is not a secure context, so it cannot hash the text it shows" )

    # -- plumbing -----------------------------------------------------------------------

    def outputs( self ) -> dict[ str, Any ]:
        markers = sorted( set( self.render.get( "text_markers", [])) | set( self.render.get( "preview_markers", [])))
        return { "revision": self.render.get( "revision" ),
                 "marker_seen": ( self.marker in markers ) if self.marker else bool( markers ),
                 "markers": markers[ :4 ], "status": self.render.get( "status" ), "chars": self.render.get( "chars" ),
                 "listed": bool( self.listed )}

    def close( self ) -> None:
        if self._chrome is not None:
            self._chrome.terminate()
            try:
                self._chrome.wait( 10 )
            except Exception:                 # noqa: BLE001
                self._chrome.kill()
        if self._profile:
            shutil.rmtree( self._profile, ignore_errors=True )

    def _eval( self, js: str ) -> Any:
        r = self._ch.call( "Runtime.evaluate", { "expression": js, "awaitPromise": True, "returnByValue": True })
        return ( r or {}).get( "result", {}).get( "value" )


def main( argv: list[ str ] | None = None ) -> int:
    ap = argparse.ArgumentParser( description=__doc__.splitlines()[ 0 ])
    ap.add_argument( "--filename", help=f"the diagnostic note to open (default: the newest { STEM }* note listed)" )
    ap.add_argument( "--marker", help="the NOTESDIAG marker the page should show" )
    ap.add_argument( "--url", default=DASHBOARD )
    ap.add_argument( "--port", type=int, default=PORT, help="headless Chrome's own debug port" )
    ap.add_argument( "--wait", type=float, default=20.0, help="seconds to wait for the page and the marker" )
    args = ap.parse_args( argv )
    if args.filename is not None and not Path( args.filename ).name.startswith( STEM ):
        print( f"refused: only the { STEM } diagnostic note is opened, not { args.filename }" )
        return 2
    if args.marker is not None and not MARKER.match( args.marker ):
        print( f"refused: a marker is NOTESDIAG-<12 hex>, not { args.marker }" )
        return 2

    check   = NotesRenderCheck( args.url, args.filename, args.marker, args.port, args.wait )
    tracer  = default_tracer()
    adw_id  = uuid.uuid4().hex[ :8 ]
    try:
        with AdwRunTrace( tracer, "adw_notes_render_check", adw_id,
                          inputs={ "filename": args.filename, "marker": args.marker, "url": args.url }) as hooks:
            render = tracer.begin( "browser.render", f"browser.render:{ adw_id }", interface="INotesRenderCheck",
                                   implementation="headless-chrome", module="adw_notes_render_check",
                                   label="Notes page render check", inputs={ "filename": args.filename,
                                   "expected_marker": args.marker, "url": args.url }, adw_id=adw_id,
                                   deps=[ f"adw.run:{ adw_id }" ])
            hooks.closing( lambda reason: render.done or render.fail( f"the run ended first: { reason }" ))
            try:
                hooks.phase( "launch_browser", check.launch_browser, "start a headless Chrome of its own" )()
                hooks.phase( "open_note", check.open_note, "open the diagnostic note on the Notes page" )()
                hooks.phase( "read_render", check.read_render, "hash what the editor shows" )()
            except RenderCheckError as exc:
                render.fail( str( exc ), outputs=check.outputs())
                print( f"render check failed: { exc }" )
                return 2

            def record() -> dict[ str, Any ]:
                out = check.outputs()
                ok = bool( out[ "revision" ]) and out[ "marker_seen" ]
                if ok:
                    render.succeed( { **out, "filename": check.filename }, f"rendered revision { out[ 'revision' ][ :12 ]}" )
                else:
                    render.fail( "the page did not show " + ( args.marker or "a diagnostic marker" ),
                                 outputs={ **out, "filename": check.filename })
                return out
            out = hooks.phase( "record", record, "record the render in the object trace" )()
    finally:
        check.close()
    print( f"{ check.filename }: rendered revision { str( out[ 'revision' ] or 'none' )[ :12 ]}, "
           f"marker seen { 'yes' if out[ 'marker_seen' ] else 'no' }, status { out[ 'status' ]!r } "
           f"(object browser.render:{ adw_id })" )
    return 0 if out[ "revision" ] and out[ "marker_seen" ] else 1


if __name__ == "__main__":
    sys.exit( main())

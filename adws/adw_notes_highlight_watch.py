#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "selenium"]
# ///
"""ADW Notes Highlight Watch -- which lines of a note does the dashboard's Notes page show highlighted, live?

Opens the Notes page in headless Chrome driven by selenium-headless's `WebBrowserManager` (its `headless=True`
option; a throwaway profile, never the user's window), opens one note, and reads every highlighted line off the
rendered preview: saved `{{highlight:name}}` spans and the deed page's yellow review marks alike, since both are
`<mark>` elements. With `--until-clear TEXT` it keeps the tab open and does not touch it -- no reload -- waiting
for the page's own 3 s poll to drop the highlight on TEXT, which is the hands-free "I fixed it, did it clear?"
check.

It records one `browser.highlights:<adw_id>` object in the object trace: counts and kinds only. The highlighted
text is printed to the terminal for the person asking, but never written to the trace.

    Phases:  LaunchBrowser -> OpenNote -> ReadHighlights -> Record

    lancedb_memory/.venv/bin/python adws/adw_notes_highlight_watch.py --filename F [--url U] [--until-clear TEXT]

Exit status 0 when the page was read (and, with --until-clear, TEXT is no longer highlighted), 1 when TEXT is still
highlighted at the deadline, 2 when the check could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( HERE ))
sys.path.insert( 0, str( HERE.parent / "lancedb_memory" ))
SELENIUM_HEADLESS_ROOT = "/home/adamsl/selenium-headless"

from object_trace.adw_hooks import AdwRunTrace               # noqa: E402
from object_trace.factory import default_tracer              # noqa: E402

DASHBOARD = "http://127.0.0.1:8765/"

OPEN_NOTES = "[ ...document.querySelectorAll( 'button' )].find( b => b.textContent.trim() === 'NOTES' )?.click()"
CLICK_NOTE = """const b = [ ...document.querySelectorAll( '#notes-landing button' )].find( b => b.textContent.includes( arguments[ 0 ] ));
  if ( b ) b.click(); return !!b;"""
MARKS = """return [ ...document.querySelectorAll( '#notes-editor-preview mark' )].map( m => ({
  kind: ( m.className.match( /notes-deed-review-([a-z-]+)/ ) || [] )[ 1 ] || 'saved', text: m.textContent.trim() }));"""


class WatchError( RuntimeError ):
    pass


class HighlightWatch:
    """The phases; each one is a method so the ADW wrapper can trace it."""

    def __init__( self, url: str, filename: str, until_clear: str | None, wait: float ) -> None:
        self.url, self.filename, self.until_clear, self.wait = url, filename, until_clear, wait
        self._browser: Any = None
        self.marks: list[ dict[ str, str ]] = []
        self.cleared: bool | None = None

    def launch_browser( self ) -> None:
        if SELENIUM_HEADLESS_ROOT not in sys.path:
            sys.path.insert( 0, SELENIUM_HEADLESS_ROOT )
        from browser_manager import WebBrowserManager            # noqa: PLC0415  (a separate, unpackaged repository)
        self._browser = WebBrowserManager( headless=True )

    def open_note( self ) -> None:
        driver = self._browser.driver
        self._browser.navigate_to_page( self.url )
        self._until( lambda: driver.execute_script( f"return !!( { OPEN_NOTES } || true )" ) and
                     driver.execute_script( "return !!document.getElementById( 'notes-landing' )" ), "the Notes page" )
        driver.execute_script( OPEN_NOTES )
        self._until( lambda: driver.execute_script( CLICK_NOTE, self.filename ), f"{ self.filename } in the Notes list" )
        self._until( lambda: driver.execute_script( "return ( document.getElementById( 'notes-editor-preview' ) || {} ).textContent" ),
                     "the note's preview" )

    def read_highlights( self ) -> None:
        driver = self._browser.driver
        deadline = time.monotonic() + self.wait
        while True:
            self.marks = driver.execute_script( MARKS )
            if self.until_clear is None:
                return
            self.cleared = not any( self.until_clear in m[ "text" ] for m in self.marks )
            if self.cleared or time.monotonic() > deadline:
                return
            time.sleep( 1 )                     # no reload: the page's own poll must do it

    def outputs( self ) -> dict[ str, Any ]:
        kinds: dict[ str, int ] = {}
        for m in self.marks:
            kinds[ m[ "kind" ]] = kinds.get( m[ "kind" ], 0 ) + 1
        return { "highlighted": len( self.marks ), "kinds": kinds, "cleared": self.cleared }

    def close( self ) -> None:
        if self._browser is not None:
            self._browser.quit()

    def _until( self, probe, what: str ) -> None:
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline:
            try:
                if probe():
                    return
            except Exception:                  # noqa: BLE001 -- the page is still re-rendering
                pass
            time.sleep( 0.5 )
        raise WatchError( f"{ what } did not appear within { self.wait:.0f} s (is the dashboard at { self.url }?)" )


def main( argv: list[ str ] | None = None ) -> int:
    ap = argparse.ArgumentParser( description=__doc__.splitlines()[ 0 ])
    ap.add_argument( "--filename", required=True, help="the note to open, as the Notes list shows it" )
    ap.add_argument( "--url", default=DASHBOARD )
    ap.add_argument( "--until-clear", help="wait (no reload) until no highlighted line contains this text" )
    ap.add_argument( "--wait", type=float, default=30.0, help="seconds to wait for the page, and for --until-clear" )
    args = ap.parse_args( argv )

    watch, tracer, adw_id = HighlightWatch( args.url, args.filename, args.until_clear, args.wait ), default_tracer(), uuid.uuid4().hex[ :8 ]
    try:
        with AdwRunTrace( tracer, "adw_notes_highlight_watch", adw_id,
                          inputs={ "filename": args.filename, "url": args.url, "until_clear": bool( args.until_clear )}) as hooks:
            obj = tracer.begin( "browser.highlights", f"browser.highlights:{ adw_id }", interface="INotesHighlightWatch",
                                implementation="selenium-headless", module="adw_notes_highlight_watch",
                                label="Notes page highlight watch", inputs={ "filename": args.filename, "url": args.url },
                                adw_id=adw_id, deps=[ f"adw.run:{ adw_id }" ])
            hooks.closing( lambda reason: obj.done or obj.fail( f"the run ended first: { reason }" ))
            try:
                hooks.phase( "launch_browser", watch.launch_browser, "start headless Chrome through WebBrowserManager" )()
                hooks.phase( "open_note", watch.open_note, "open the note on the Notes page" )()
                hooks.phase( "read_highlights", watch.read_highlights, "read the highlighted lines off the preview" )()
            except WatchError as exc:
                obj.fail( str( exc ), outputs=watch.outputs())
                print( f"highlight watch failed: { exc }" )
                return 2

            def record() -> dict[ str, Any ]:
                out = watch.outputs()
                if watch.cleared is False:
                    obj.fail( f"{ args.until_clear!r} was still highlighted at the deadline", outputs={ **out, "filename": args.filename })
                else:
                    obj.succeed( { **out, "filename": args.filename }, f"{ out[ 'highlighted' ]} highlighted line(s)" )
                return out
            out = hooks.phase( "record", record, "record the counts in the object trace" )()
    finally:
        watch.close()
    for m in watch.marks:
        print( f"  [{ m[ 'kind' ]}] { m[ 'text' ][ :110 ]}" )
    verdict = "" if watch.cleared is None else ( f"; { args.until_clear!r} is " + ( "no longer highlighted (cleared live, no reload)" if watch.cleared else "STILL highlighted" ))
    print( f"{ args.filename }: { out[ 'highlighted' ]} highlighted line(s){ verdict } (object browser.highlights:{ adw_id })" )
    return 1 if watch.cleared is False else 0


if __name__ == "__main__":
    sys.exit( main())

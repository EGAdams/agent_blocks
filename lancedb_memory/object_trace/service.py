"""The object inspection service: the only reader of the trace store, for the debugger UI and for Toyota alike.

    .venv/bin/python -m object_trace.service --port 8790 --ui ~/the-factory/public

Every request opens the store read-only (`mode=ro`) and closes it again, so
no route can write and no connection is shared between threads. Routes:

    GET /api/health                     store counts, schema, build ids, last event time
    GET /api/snapshot?kind=&state=&q=&run=&task=&limit=
                                        objects grouped by run, plus recent sssf ADW sessions
    GET /api/events?after_seq=&limit=   what changed since a cursor, and the objects it touched
    GET /api/objects/<id>               one object's view, its last events and dependents
    GET /api/ask/<question>?subject=    latest_notes_update | where_stopped | object_state | saved_and_rendered
    GET /api/inventory                  ADW coverage: every ADW sssf.db has seen, and whether it is traced
    GET /fx/object/selectAll            the-factory's data-source contract, for <log-viewer>/<monitor-led>
    GET /fx/object/select?object_view_id=
    GET /<file>                         the-factory's public/ directory, read-only

Every response is JSON except static files; the UI writes text with
`textContent` only, and everything the store holds was redacted before it
was written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import mimetypes
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .coverage import adw_coverage
from .adapters.sqlite_store import SqliteTraceStore
from .adapters.sssf_reader import SssfReader
from .factory import CHECKOUT, SSSF_DB, trace_db_path
from .inspector import Inspector
from .models import ObjectView
from .tracer import SystemClock

LED = { "succeeded": "lightgreen", "failed": "#fb6666", "stale": "#c9a0ff", "waiting": "lightblue",
        "heartbeat": "lightyellow", "progress": "lightyellow", "created": "#dddddd", "cancelled": "#bbbbbb" }
QUESTIONS = ( "latest_notes_update", "where_stopped", "object_state", "saved_and_rendered" )


def source_build() -> str:
    """The git commit of this checkout plus a hash of object_trace's own source, so an edited-but-uncommitted
    service never claims to be the committed one."""
    try:
        sha = subprocess.run([ "git", "-C", str( CHECKOUT ), "rev-parse", "--short", "HEAD" ], capture_output=True,
                             text=True, timeout=3 ).stdout.strip() or "nogit"
    except Exception:                         # noqa: BLE001
        sha = "nogit"
    h = hashlib.sha256()
    for f in sorted( Path( __file__ ).parent.rglob( "*.py" )):
        if "tests" not in f.parts:
            h.update( f.read_bytes())
    return f"{ sha }+{ h.hexdigest()[ :8 ]}"


def file_build( path: Path ) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return "absent"
    return f"{ hashlib.sha256( data ).hexdigest()[ :12 ]}@{ time.strftime( '%Y-%m-%dT%H:%M:%S', time.localtime( path.stat().st_mtime ))}"


def fx_row( v: ObjectView ) -> dict:
    """One object in the-factory's `{ id, object_view_id, object_data }` shape (object_data is a JSON string)."""
    o = v.object
    logs = [{ "timestamp": time.strftime( "%Y-%m-%d %H:%M:%S", time.localtime( e.at )), "id": str( e.seq ),
              "message": f"{ e.kind }: { e.message }"[ :500 ], "method": e.kind } for e in v.events ]
    data = { "object_view_id": o.object_id, "logObjects": logs,
             "monitorLed": { "classObject": { "background_color": LED.get( v.display, "#dddddd" ), "text_align": "center",
                                              "margin_top": "0px", "color": "black" },
                             "ledText": f"{ v.display }: { v.reason }"[ :200 ], "RUNNING_COLOR": "lightyellow",
                             "PASS_COLOR": "lightgreen", "FAIL_COLOR": "#fb6666" }}
    return { "id": o.object_id, "object_view_id": o.object_id, "object_data": json.dumps( data )}


ADWS_DIR = Path( os.environ.get( "OBJECT_TRACE_ADWS_DIR", str( Path( __file__ ).resolve().parents[ 2 ] / "adws" )))


class InspectionApp:
    """Route table, kept apart from the HTTP handler so the tests can call it without a socket."""

    def __init__( self, db: Path, sssf: Path | None, ui: Path | None, build: str | None = None,
                  adws: Path | None = None ) -> None:
        self.adws  = Path( adws ) if adws else ADWS_DIR
        self.db    = Path( db )
        self.sssf  = SssfReader( sssf ) if sssf else None
        self.ui    = Path( ui ).resolve() if ui else None
        self.build = build or source_build()
        self.clock = SystemClock()
        if not self.db.exists():              # make the empty, migrated file once, so read-only opens succeed
            SqliteTraceStore( self.db ).close()

    def handle( self, path: str, query: dict[ str, list[ str ]]) -> tuple[ int, str, bytes ]:
        q = lambda key, default=None: ( query.get( key ) or [ default ])[ 0 ]
        if path.startswith( "/api/" ) or path.startswith( "/fx/" ):
            try:
                return self._json( 200, self._api( path, q ))
            except LookupError as exc:
                return self._json( 404, { "error": str( exc )[ :200 ]})
            except ValueError as exc:
                return self._json( 400, { "error": str( exc )[ :200 ]})
            except Exception as exc:              # noqa: BLE001 -- a broken route reports, the service stays up
                return self._json( 500, { "error": f"{ type( exc ).__name__ }: { str( exc )[ :200 ]}" })
        return self._static( path )

    def _api( self, path: str, q ) -> object:
        store = SqliteTraceStore( self.db, read_only=True )
        try:
            ins = Inspector( store, self.clock, self.sssf )
            if path == "/api/health":
                h = ins.health( self.build, str( self.db )).model_dump()
                h[ "ui_build" ] = file_build( self.ui / "dist" / "bundle.js" ) if self.ui else "none"
                return h
            if path == "/api/snapshot":
                return ins.snapshot( kind=q( "kind" ) or None, state=q( "state" ) or None, text=q( "q" ) or None,
                                     run_id=q( "run" ) or None, task_id=q( "task" ) or None, limit=_int( q( "limit" ), 300 ))
            if path == "/api/events":
                return ins.changes( _int( q( "after_seq" ), 0 ), _int( q( "limit" ), 200 ))
            if path.startswith( "/api/objects/" ):
                v = ins.view( unquote( path[ len( "/api/objects/" ): ]), _int( q( "events" ), 50 ))
                if v is None:
                    raise LookupError( "no such object" )
                return v.model_dump()
            if path.startswith( "/api/ask/" ):
                name = path[ len( "/api/ask/" ): ]
                if name not in QUESTIONS:
                    raise LookupError( f"unknown question; ask one of { ', '.join( QUESTIONS )}" )
                return ins.ask( name, q( "subject" ) or None ).model_dump()
            if path == "/api/inventory":
                return self._inventory( store )
            if path == "/fx/object/selectAll":
                views = [ ins.view( o.object_id, 30 ) for o in store.objects( limit=_int( q( "limit" ), 50 ))]
                return [ fx_row( v ) for v in views if v is not None ]
            if path == "/fx/object/select":
                v = ins.view( q( "object_view_id" ) or "", 100 )
                if v is None:
                    raise LookupError( "no such object" )
                return fx_row( v )
            raise LookupError( "no such route" )
        finally:
            store.close()

    def _inventory( self, store: SqliteTraceStore ) -> dict:
        runs: dict[ str, int ] = {}
        for o in store.objects( kind="adw.run", limit=2000 ):
            runs[ o.implementation ] = runs.get( o.implementation, 0 ) + 1
        seen = {( name ): ( count, last ) for name, count, last in ( self.sssf.adw_names( 500 ) if self.sssf else [])}
        rows = []
        for c in adw_coverage( self.adws ):
            count, last = seen.get( c.adw, ( 0, None ))
            rows.append({ "adw": c.adw, "hook": c.hook, "via": c.via, "traced_runs": runs.get( c.adw, 0 ),
                          "sssf_sessions": count, "last_started": last })
        on_disk = { r[ "adw" ] for r in rows }
        totals: dict[ str, int ] = {}
        for r in rows:
            totals[ r[ "hook" ]] = totals.get( r[ "hook" ], 0 ) + 1
        return { "sssf": self.sssf.status() if self.sssf else "absent", "adws_dir": str( self.adws ), "totals": totals,
                 "adws": rows, "seen_but_not_on_disk": sorted(( set( seen ) | set( runs )) - on_disk )}

    def _static( self, path: str ) -> tuple[ int, str, bytes ]:
        if self.ui is None:
            return self._json( 404, { "error": "no UI directory configured" })
        target = ( self.ui / unquote( path ).lstrip( "/" )).resolve()
        if target.is_dir():
            target = target / "index.html"
        if self.ui not in target.parents and target != self.ui or not target.is_file():
            return self._json( 404, { "error": "not found" })
        return 200, mimetypes.guess_type( target.name )[ 0 ] or "application/octet-stream", target.read_bytes()

    @staticmethod
    def _json( status: int, body: object ) -> tuple[ int, str, bytes ]:
        return status, "application/json", json.dumps( body, default=str ).encode()


def _int( value: str | None, default: int ) -> int:
    if value in ( None, "" ):
        return default
    try:
        return int( value )
    except ValueError as exc:
        raise ValueError( f"not a number: { value[ :20 ]!r}" ) from exc


def make_handler( app: InspectionApp ) -> type[ BaseHTTPRequestHandler ]:
    class Handler( BaseHTTPRequestHandler ):
        server_version = "object-inspector"

        def do_GET( self ) -> None:              # noqa: N802
            url = urlparse( self.path )
            status, ctype, body = app.handle( url.path, parse_qs( url.query ))
            self.send_response( status )
            self.send_header( "Content-Type", ctype + ( "; charset=utf-8" if ctype.startswith(( "text/", "application/j" )) else "" ))
            self.send_header( "Content-Length", str( len( body )))
            self.send_header( "Cache-Control", "no-store" )
            self.send_header( "X-Content-Type-Options", "nosniff" )
            self.end_headers()
            self.wfile.write( body )

        def log_message( self, fmt: str, *args: object ) -> None:
            if args and str( args[ 1 ] if len( args ) > 1 else "" ).startswith(( "4", "5" )):
                sys.stderr.write( "object-inspector: " + ( fmt % args ) + "\n" )
    return Handler


def main( argv: list[ str ] | None = None ) -> int:
    p = argparse.ArgumentParser( description="Serve the object trace to the debugger UI and to Toyota." )
    p.add_argument( "--host", default="127.0.0.1" )
    p.add_argument( "--port", type=int, default=8790 )
    p.add_argument( "--db", type=Path, default=None )
    p.add_argument( "--sssf", type=Path, default=SSSF_DB )
    p.add_argument( "--ui", type=Path, default=Path.home() / "the-factory" / "public" )
    a = p.parse_args( argv )
    app = InspectionApp( a.db or trace_db_path(), a.sssf, a.ui if a.ui.is_dir() else None )
    server = ThreadingHTTPServer(( a.host, a.port ), make_handler( app ))
    print( f"object-inspector { app.build } on http://{ a.host }:{ a.port }/ (store { app.db })", flush=True )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit( main())

"""Maintenance for the trace store: schema migration (and rollback), retention, and a health line.

    .venv/bin/python -m object_trace.cli migrate [--to N]   # --to 0 drops object_trace's own tables, nothing else
    .venv/bin/python -m object_trace.cli prune [--days 14]
    .venv/bin/python -m object_trace.cli health
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .adapters.sqlite_store import LATEST, SqliteTraceStore, migrate, version_of
from .factory import trace_db_path


def main( argv: list[ str ] | None = None ) -> int:
    p = argparse.ArgumentParser( description=__doc__.splitlines()[ 0 ])
    p.add_argument( "--db", type=Path, default=None )
    sub = p.add_subparsers( dest="cmd", required=True )
    m = sub.add_parser( "migrate" )
    m.add_argument( "--to", type=int, default=LATEST )
    r = sub.add_parser( "prune" )
    r.add_argument( "--days", type=float, default=14 )
    sub.add_parser( "health" )
    a = p.parse_args( argv )
    db = a.db or trace_db_path()
    if a.cmd == "migrate":
        conn = sqlite3.connect( str( db ))
        before = version_of( conn )
        after = migrate( conn, a.to )
        conn.close()
        print( f"{ db }: schema { before } -> { after }" )
        return 0
    if not db.exists():
        print( f"{ db }: no trace store yet" )
        return 1
    if a.cmd == "prune":
        store = SqliteTraceStore( db )
        events, objects = store.prune( a.days * 86400 )
        store.close()
        print( f"pruned { events } event(s) and { objects } object(s) older than { a.days:g} day(s)" )
        return 0
    store = SqliteTraceStore( db, read_only=True )
    objects, events, max_seq, last = store.counts()
    print( json.dumps({ "db": str( db ), "schema": store.schema_version(), "objects": objects, "events": events,
                        "max_seq": max_seq, "last_event_at": last }))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit( main())

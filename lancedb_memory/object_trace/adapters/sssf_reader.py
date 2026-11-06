"""SssfReader: the ADW session database (`adws/adw_data/sssf.db`), read-only, as trace objects.

Every ADW built on `adw_modules` already records its session, phases and
process there. This adapter shows them without writing anything: the file is
opened `mode=ro`, every query is bounded, and a session's liveness comes from
its `processes` row (an `ended_at` of NULL is only a belief, so the pid is
checked through the same liveness rule as everything else).
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from ..models import TraceObject
from .sqlite_store import MAX_LIMIT

_STATE = { "success": "succeeded", "pass": "succeeded", "passed": "succeeded", "done": "succeeded",
           "fail": "failed", "failed": "failed", "error": "failed",
           "running": "running", "started": "running", "cancelled": "cancelled" }


def epoch( text: str | None ) -> float | None:
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat( text ).timestamp()
    except ValueError:
        return None


class SssfReader:
    def __init__( self, path: Path ) -> None:
        self.path = Path( path )

    def __repr__( self ) -> str:
        return f"SssfReader({ str( self.path )!r })"

    def status( self ) -> str:
        if not self.path.exists():
            return "absent"
        try:
            with self._connect() as conn:
                conn.execute( "SELECT 1 FROM sessions LIMIT 1" ).fetchall()
        except sqlite3.Error as exc:
            return f"unreadable: { type( exc ).__name__ }"
        return "readable"

    def sessions( self, *, adw_name: str | None = None, limit: int = 50 ) -> list[ TraceObject ]:
        where, args = ( "WHERE s.adw_name = ?", [ adw_name ]) if adw_name else ( "", [])
        return [ self._session( r ) for r in self._rows(
            "SELECT s.*, p.pid, p.ended_at AS proc_ended FROM sessions s LEFT JOIN processes p "
            f"ON p.adw_id = s.adw_id AND p.kind = 'adw' { where } ORDER BY s.started_at DESC LIMIT ?",
            [ *args, max( 1, min( limit, MAX_LIMIT ))])]

    def session( self, adw_id: str ) -> TraceObject | None:
        rows = self._rows( "SELECT s.*, p.pid, p.ended_at AS proc_ended FROM sessions s LEFT JOIN processes p "
                           "ON p.adw_id = s.adw_id AND p.kind = 'adw' WHERE s.adw_id = ? LIMIT 1", [ adw_id ])
        return self._session( rows[ 0 ]) if rows else None

    def phases( self, adw_id: str ) -> list[ TraceObject ]:
        rows = self._rows( "SELECT * FROM phases WHERE adw_id = ? ORDER BY seq LIMIT ?", [ adw_id, MAX_LIMIT ])
        out, previous = [], None
        for r in rows:
            started, ended = epoch( r[ "started_at" ]), epoch( r[ "ended_at" ])
            oid = f"sssf.phase:{ r[ 'phase_id' ]}"
            out.append( TraceObject(
                object_id=oid[ :200 ], kind="sssf.phase", implementation=str( r[ "owner" ] or "" )[ :120 ],
                label=str( r[ "description" ] or r[ "name" ] or "" )[ :200 ], created_at=started or 0.0, adw_id=adw_id,
                phase=str( r[ "name" ] or "" )[ :80 ], attempt=r[ "attempt" ], parent_id=f"sssf.run:{ adw_id }",
                deps=[ previous ] if previous else [], state=_STATE.get( str( r[ "status" ]).lower(), "created" ),
                error={ "message": str( r[ "error" ])[ :500 ]} if r[ "error" ] else None,
                last_event_at=ended or started, last_event_kind="succeeded" if ended else "progress" ))
            previous = oid[ :200 ]
        return out

    def adw_names( self, limit: int = 200 ) -> list[ tuple[ str, int, str | None ]]:
        return [( r[ 0 ], r[ 1 ], r[ 2 ]) for r in self._rows(
            "SELECT adw_name, COUNT(*), MAX( started_at ) FROM sessions GROUP BY adw_name ORDER BY 2 DESC LIMIT ?",
            [ max( 1, min( limit, MAX_LIMIT ))])]

    # -- plumbing ---------------------------------------------------------------------

    def _session( self, r: sqlite3.Row ) -> TraceObject:
        started, ended = epoch( r[ "started_at" ]), epoch( r[ "ended_at" ])
        state = _STATE.get( str( r[ "status" ] or "" ).lower(), "running" if not ended else "created" )
        return TraceObject(
            object_id=f"sssf.run:{ r[ 'adw_id' ]}"[ :200 ], kind="sssf.run", implementation=str( r[ "adw_name" ] or "" )[ :120 ],
            module=str( r[ "adw_name" ] or "" )[ :200 ], label=str( r[ "adw_name" ] or "" )[ :200 ], created_at=started or 0.0,
            pid=r[ "pid" ], adw_id=str( r[ "adw_id" ])[ :200 ], state=state, last_event_at=ended or started,
            last_event_kind="succeeded" if ended else "progress" )

    def _rows( self, sql: str, args: list ) -> list[ sqlite3.Row ]:
        if not self.path.exists():
            return []
        try:
            with self._connect() as conn:
                return conn.execute( sql, args ).fetchall()
        except sqlite3.Error:
            return []

    def _connect( self ) -> sqlite3.Connection:
        conn = sqlite3.connect( f"file:{ self.path }?mode=ro", uri=True, timeout=1.0 )
        conn.row_factory = sqlite3.Row
        conn.execute( "PRAGMA busy_timeout=1000" )
        return conn

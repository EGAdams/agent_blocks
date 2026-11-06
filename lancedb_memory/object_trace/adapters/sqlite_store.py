"""SqliteTraceStore: ITraceSink and ITraceReader over one SQLite file.

The schema is versioned in `schema_version` and changed only by MIGRATIONS,
each an (up, down) pair, so `migrate( conn, 0 )` is the rollback: it drops
exactly the tables this store made and nothing else. Writers open the file
read-write in WAL mode with a short busy timeout; the inspection service
opens it with `read_only=True` (`mode=ro`), so no read path can write.

Events are deduplicated by `event_id` (INSERT OR IGNORE). `seq` is the
store's own autoincrement, the cursor incremental readers resume from.
Retention is `prune()`: events older than the horizon go first, then
finished objects with no remaining events.

Every query is parameterized and every list is capped at MAX_LIMIT.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..models import TERMINAL, TraceEvent, TraceObject

MAX_LIMIT = 500
_TEXT = ( "interface", "implementation", "module", "label", "host", "process_session" )

MIGRATIONS: list[ tuple[ int, str, str ]] = [
    ( 1,
      """
      CREATE TABLE IF NOT EXISTS trace_objects (
        object_id TEXT PRIMARY KEY, kind TEXT NOT NULL, interface TEXT, implementation TEXT, module TEXT, label TEXT,
        created_at REAL NOT NULL, host TEXT, pid INTEGER, process_session TEXT,
        run_id TEXT, task_id TEXT, adw_id TEXT, phase TEXT, attempt INTEGER, span_id TEXT, parent_id TEXT,
        heartbeat_seconds REAL, state TEXT NOT NULL, inputs TEXT, outputs TEXT, error TEXT,
        last_event_at REAL, last_event_kind TEXT
      );
      CREATE TABLE IF NOT EXISTS trace_deps (
        object_id TEXT NOT NULL, depends_on TEXT NOT NULL, PRIMARY KEY ( object_id, depends_on )
      );
      CREATE TABLE IF NOT EXISTS trace_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, object_id TEXT NOT NULL,
        at REAL NOT NULL, kind TEXT NOT NULL, state TEXT, message TEXT, data TEXT, run_id TEXT, task_id TEXT
      );
      CREATE INDEX IF NOT EXISTS trace_objects_run  ON trace_objects ( run_id );
      CREATE INDEX IF NOT EXISTS trace_objects_task ON trace_objects ( task_id );
      CREATE INDEX IF NOT EXISTS trace_objects_kind ON trace_objects ( kind, last_event_at );
      CREATE INDEX IF NOT EXISTS trace_objects_last ON trace_objects ( last_event_at );
      CREATE INDEX IF NOT EXISTS trace_deps_on      ON trace_deps ( depends_on );
      CREATE INDEX IF NOT EXISTS trace_events_obj   ON trace_events ( object_id, seq );
      CREATE INDEX IF NOT EXISTS trace_events_task  ON trace_events ( task_id, seq );
      CREATE INDEX IF NOT EXISTS trace_events_at    ON trace_events ( at );
      """,
      """
      DROP TABLE IF EXISTS trace_events;
      DROP TABLE IF EXISTS trace_deps;
      DROP TABLE IF EXISTS trace_objects;
      """ ),
]
LATEST = MIGRATIONS[ -1 ][ 0 ]

_COLUMNS = [ "object_id", "kind", "interface", "implementation", "module", "label", "created_at", "host", "pid",
             "process_session", "run_id", "task_id", "adw_id", "phase", "attempt", "span_id", "parent_id",
             "heartbeat_seconds", "state", "inputs", "outputs", "error", "last_event_at", "last_event_kind" ]
_JSON = { "inputs", "outputs", "error" }

# An object with no run of its own belongs to the run of any object sharing its task id.
_EFFECTIVE_RUN = ( "COALESCE( o.run_id, ( SELECT s.run_id FROM trace_objects s WHERE s.task_id = o.task_id "
                   "AND s.run_id IS NOT NULL ORDER BY s.created_at LIMIT 1 ))" )


def version_of( conn: sqlite3.Connection ) -> int:
    conn.execute( "CREATE TABLE IF NOT EXISTS schema_version ( version INTEGER NOT NULL )" )
    row = conn.execute( "SELECT version FROM schema_version" ).fetchone()
    return int( row[ 0 ]) if row else 0


def migrate( conn: sqlite3.Connection, target: int = LATEST ) -> int:
    """Move the schema up or down to `target`. 0 removes every table this store made."""
    current = version_of( conn )
    if target > current:
        for version, up, _ in MIGRATIONS:
            if current < version <= target:
                conn.executescript( up )
                current = version
    elif target < current:
        for version, _, down in reversed( MIGRATIONS ):
            if target < version <= current:
                conn.executescript( down )
                current = version - 1
    conn.execute( "DELETE FROM schema_version" )
    conn.execute( "INSERT INTO schema_version ( version ) VALUES ( ? )", ( current, ))
    conn.commit()
    return current


def _bounded( limit: int ) -> int:
    return max( 1, min( int( limit ), MAX_LIMIT ))


def _like( text: str ) -> str:
    return "%" + text.replace( "\\", "\\\\" ).replace( "%", "\\%" ).replace( "_", "\\_" )[ :100 ] + "%"


class SqliteTraceStore:
    def __init__( self, path: Path, read_only: bool = False, prune_every: int = 500,
                  horizon_seconds: float = 14 * 86400 ) -> None:
        self.path       = Path( path )
        self._read_only = read_only
        self._lock      = threading.Lock()
        self._writes    = 0
        self._prune_every = prune_every
        self._horizon   = horizon_seconds
        if read_only:
            self._conn = sqlite3.connect( f"file:{ self.path }?mode=ro", uri=True, timeout=2.0, check_same_thread=False )
        else:
            self.path.parent.mkdir( parents=True, exist_ok=True )
            self._conn = sqlite3.connect( str( self.path ), timeout=2.0, check_same_thread=False )
            self._conn.execute( "PRAGMA journal_mode=WAL" )
            self._conn.execute( "PRAGMA synchronous=NORMAL" )
            migrate( self._conn )
        self._conn.execute( "PRAGMA busy_timeout=2000" )
        self._conn.row_factory = sqlite3.Row

    def __repr__( self ) -> str:
        return f"SqliteTraceStore({ str( self.path )!r }, read_only={ self._read_only })"

    def close( self ) -> None:
        self._conn.close()

    # -- ITraceSink ---------------------------------------------------------------------

    def put_object( self, obj: TraceObject ) -> None:
        row = obj.model_dump()
        values = [ json.dumps( row[ c ]) if c in _JSON and row[ c ] is not None else row[ c ] for c in _COLUMNS ]
        updates = ", ".join( f"{ c } = excluded.{ c }" for c in _COLUMNS if c not in ( "object_id", "created_at" ))
        with self._lock:
            self._conn.execute( f"INSERT INTO trace_objects ( { ', '.join( _COLUMNS )} ) VALUES ( { ', '.join( '?' * len( _COLUMNS ))} ) "
                                f"ON CONFLICT ( object_id ) DO UPDATE SET { updates }", values )
            self._conn.executemany( "INSERT OR IGNORE INTO trace_deps ( object_id, depends_on ) VALUES ( ?, ? )",
                                    [( obj.object_id, d ) for d in obj.deps ])
            self._conn.commit()

    def put_event( self, event: TraceEvent ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO trace_events ( event_id, object_id, at, kind, state, message, data, run_id, task_id ) "
                "VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ? )",
                ( event.event_id, event.object_id, event.at, event.kind, event.state, event.message,
                  json.dumps( event.data ) if event.data is not None else None, event.run_id, event.task_id ))
            stored = cur.rowcount == 1
            if stored:
                self._conn.execute(
                    "INSERT OR IGNORE INTO trace_objects ( object_id, kind, created_at, state, run_id, task_id ) "
                    "VALUES ( ?, 'unknown', ?, 'created', ?, ? )", ( event.object_id, event.at, event.run_id, event.task_id ))
                self._conn.execute(
                    "UPDATE trace_objects SET last_event_at = MAX( COALESCE( last_event_at, 0 ), ? ), "
                    "last_event_kind = CASE WHEN COALESCE( last_event_at, 0 ) <= ? THEN ? ELSE last_event_kind END, "
                    "state = COALESCE( ?, state ) WHERE object_id = ?",
                    ( event.at, event.at, event.kind, event.state, event.object_id ))
            self._conn.commit()
            self._writes += 1
            due = self._prune_every and self._writes % self._prune_every == 0
        if due:
            self.prune()
        return stored

    def prune( self, horizon_seconds: float | None = None, now: float | None = None ) -> tuple[ int, int ]:
        """Retention: drop events older than the horizon, then finished objects with nothing left. (events, objects)"""
        cutoff = ( now or time.time()) - ( horizon_seconds or self._horizon )
        terminal = tuple( TERMINAL )
        with self._lock:
            events = self._conn.execute( "DELETE FROM trace_events WHERE at < ?", ( cutoff, )).rowcount
            objects = self._conn.execute(
                f"DELETE FROM trace_objects WHERE COALESCE( last_event_at, created_at ) < ? "
                f"AND ( state IN ( { ', '.join( '?' * len( terminal ))} ) OR kind = 'unknown' ) "
                "AND object_id NOT IN ( SELECT DISTINCT object_id FROM trace_events )", ( cutoff, *terminal )).rowcount
            self._conn.execute( "DELETE FROM trace_deps WHERE object_id NOT IN ( SELECT object_id FROM trace_objects )" )
            self._conn.commit()
        return events, objects

    # -- ITraceReader -------------------------------------------------------------------

    def schema_version( self ) -> int:
        try:
            row = self._conn.execute( "SELECT version FROM schema_version" ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int( row[ 0 ]) if row else 0

    def counts( self ) -> tuple[ int, int, int, float | None ]:
        objects = self._conn.execute( "SELECT COUNT(*) FROM trace_objects" ).fetchone()[ 0 ]
        events, max_seq, last = self._conn.execute( "SELECT COUNT(*), COALESCE( MAX( seq ), 0 ), MAX( at ) FROM trace_events" ).fetchone()
        return int( objects ), int( events ), int( max_seq ), last

    def object( self, object_id: str ) -> TraceObject | None:
        rows = self._select( "o.object_id = ?", [ object_id ], 1 )
        return rows[ 0 ] if rows else None

    def objects( self, *, run_id: str | None = None, task_id: str | None = None, kind: str | None = None,
                 state: str | None = None, text: str | None = None, since: float | None = None,
                 limit: int = 100 ) -> list[ TraceObject ]:
        where, args = [], []
        if run_id:
            where.append( f"{ _EFFECTIVE_RUN } = ?" )
            args.append( run_id )
        if task_id:
            where.append( "o.task_id = ?" )
            args.append( task_id )
        if kind:
            if kind.endswith( "*" ):
                where.append( "o.kind LIKE ? ESCAPE '\\'" )
                args.append( _like( kind[ :-1 ])[ 1: ])
            else:
                where.append( "o.kind = ?" )
                args.append( kind )
        if state:
            where.append( "o.state = ?" )
            args.append( state )
        if text:
            where.append( "( o.object_id LIKE ? ESCAPE '\\' OR o.label LIKE ? ESCAPE '\\' OR o.task_id LIKE ? ESCAPE '\\' "
                          "OR o.run_id LIKE ? ESCAPE '\\' OR o.implementation LIKE ? ESCAPE '\\' )" )
            args += [ _like( text )] * 5
        if since is not None:
            where.append( "COALESCE( o.last_event_at, o.created_at ) >= ?" )
            args.append( since )
        return self._select( " AND ".join( where ) or "1 = 1", args, _bounded( limit ))

    def events( self, *, after_seq: int = 0, object_id: str | None = None, run_id: str | None = None,
                task_id: str | None = None, limit: int = 200 ) -> list[ TraceEvent ]:
        where, args = [ "e.seq > ?" ], [ int( after_seq )]
        if object_id:
            where.append( "e.object_id = ?" )
            args.append( object_id )
        if task_id:
            where.append( "e.task_id = ?" )
            args.append( task_id )
        if run_id:
            where.append( "( e.run_id = ? OR e.task_id IN ( SELECT task_id FROM trace_objects WHERE run_id = ? AND task_id IS NOT NULL ))" )
            args += [ run_id, run_id ]
        rows = self._conn.execute( f"SELECT e.* FROM trace_events e WHERE { ' AND '.join( where )} ORDER BY e.seq LIMIT ?",
                                   [ *args, _bounded( limit )]).fetchall()
        return [ _event( r ) for r in rows ]

    def latest_events( self, object_id: str, limit: int = 50 ) -> list[ TraceEvent ]:
        rows = self._conn.execute( "SELECT * FROM trace_events WHERE object_id = ? ORDER BY seq DESC LIMIT ?",
                                   ( object_id, _bounded( limit ))).fetchall()
        return [ _event( r ) for r in reversed( rows )]

    def dependents( self, object_id: str ) -> list[ str ]:
        rows = self._conn.execute( "SELECT object_id FROM trace_deps WHERE depends_on = ? LIMIT ?", ( object_id, MAX_LIMIT )).fetchall()
        children = self._conn.execute( "SELECT object_id FROM trace_objects WHERE parent_id = ? LIMIT ?", ( object_id, MAX_LIMIT )).fetchall()
        return sorted({ r[ 0 ] for r in rows } | { r[ 0 ] for r in children })

    def runs( self, limit: int = 50 ) -> list[ dict ]:
        rows = self._conn.execute(
            f"SELECT COALESCE( { _EFFECTIVE_RUN }, 'process:' || COALESCE( o.process_session, '?' )) AS run, "
            "COUNT(*) AS objects, MIN( o.created_at ) AS first_at, MAX( COALESCE( o.last_event_at, o.created_at )) AS last_at, "
            "GROUP_CONCAT( DISTINCT o.state ) AS states, GROUP_CONCAT( DISTINCT o.kind ) AS kinds "
            "FROM trace_objects o GROUP BY run ORDER BY last_at DESC LIMIT ?", ( _bounded( limit ), )).fetchall()
        return [{ "run_id": r[ "run" ], "objects": r[ "objects" ], "first_at": r[ "first_at" ], "last_at": r[ "last_at" ],
                  "states": sorted(( r[ "states" ] or "" ).split( "," )), "kinds": sorted(( r[ "kinds" ] or "" ).split( "," ))}
                for r in rows ]

    # -- rows -----------------------------------------------------------------------------

    def _select( self, where: str, args: list, limit: int ) -> list[ TraceObject ]:
        rows = self._conn.execute(
            f"SELECT o.*, { _EFFECTIVE_RUN } AS effective_run FROM trace_objects o WHERE { where } "
            "ORDER BY COALESCE( o.last_event_at, o.created_at ) DESC LIMIT ?", [ *args, limit ]).fetchall()
        out = []
        for r in rows:
            deps = [ d[ 0 ] for d in self._conn.execute( "SELECT depends_on FROM trace_deps WHERE object_id = ? LIMIT 20",
                                                         ( r[ "object_id" ], )).fetchall()]
            data = { c: r[ c ] for c in _COLUMNS }
            for c in _JSON:
                data[ c ] = json.loads( data[ c ]) if data[ c ] else None
            for c in _TEXT:                              # a stub row made by an early event has NULLs here
                data[ c ] = data[ c ] or ""
            data[ "run_id" ] = r[ "effective_run" ]          # inherited through the task id when it has none
            data[ "deps" ]   = deps
            if data[ "state" ] not in ( "created", "running", "waiting", *TERMINAL ):
                data[ "state" ] = "created"
            out.append( TraceObject.model_validate( data ))
        return out


def _event( r: sqlite3.Row ) -> TraceEvent:
    return TraceEvent( event_id=r[ "event_id" ], object_id=r[ "object_id" ], at=r[ "at" ], kind=r[ "kind" ],
                       state=r[ "state" ], message=r[ "message" ] or "", data=json.loads( r[ "data" ]) if r[ "data" ] else None,
                       run_id=r[ "run_id" ], task_id=r[ "task_id" ], seq=r[ "seq" ])

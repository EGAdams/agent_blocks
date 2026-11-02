"""SqliteJobRegistry: the durable IJobRegistry. One row per job id; no document content, ever.

`begin()` is the duplicate guard: the first caller with a job id creates the
row, every later caller gets the existing row back and `created=False`, so a
retried bridge task finds its own earlier attempt instead of printing again.
`recover()` is the crash rule: a job left in `submitting` -- the process died
between handing pages to the spooler and hearing back -- becomes `ambiguous`,
and an ambiguous job is never resubmitted by anything here. Only one job runs
at a time (the caller holds `adapters/print_lock.py`'s lock), which is what
makes `recover()` safe to call at the start of every run.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from .errors import StaleState
from .models import JobRecord, JobState

SCHEMA = """
CREATE TABLE IF NOT EXISTS print_jobs (
    job_id      TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    printer_id  TEXT,
    printer     TEXT,
    copies      INTEGER NOT NULL DEFAULT 1,
    pages       INTEGER,
    sha256      TEXT NOT NULL DEFAULT '',
    bytes       INTEGER NOT NULL DEFAULT 0,
    spooler_id  INTEGER,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    submitted_at TEXT
);
CREATE TABLE IF NOT EXISTS print_events (
    job_id  TEXT NOT NULL,
    at      TEXT NOT NULL,
    state   TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
);
"""
FIELDS = ( "printer_id", "printer", "copies", "pages", "sha256", "bytes", "spooler_id" )


class SqliteJobRegistry:
    def __init__( self, path: Path, now: Callable[[], datetime ] = lambda: datetime.now().astimezone()) -> None:
        path.parent.mkdir( mode=0o700, parents=True, exist_ok=True )
        self._path = path
        self._now  = now
        with self._db() as db:
            db.executescript( SCHEMA )
        path.chmod( 0o600 )

    def _db( self ) -> sqlite3.Connection:
        db = sqlite3.connect( self._path, timeout=10.0, isolation_level=None )
        db.row_factory = sqlite3.Row
        return db

    # -- IJobRegistry -----------------------------------------------------------

    def get( self, job_id: str ) -> JobRecord | None:
        db = self._db()
        try:
            row = db.execute( "SELECT * FROM print_jobs WHERE job_id = ?", ( job_id, )).fetchone()
        finally:
            db.close()
        return None if row is None else self._record( row )

    def begin( self, record: JobRecord ) -> tuple[ JobRecord, bool ]:
        db = self._db()
        try:
            db.execute( "BEGIN IMMEDIATE" )
            row = db.execute( "SELECT * FROM print_jobs WHERE job_id = ?", ( record.job_id, )).fetchone()
            if row is not None:
                db.execute( "COMMIT" )
                return self._record( row ), False
            at = self._now().isoformat()
            db.execute( "INSERT INTO print_jobs ( job_id, state, kind, title, printer_id, printer, copies, pages, sha256,"
                        " bytes, spooler_id, detail, created_at, updated_at ) VALUES ( ?,?,?,?,?,?,?,?,?,?,?,?,?,? )",
                        ( record.job_id, record.state.value, record.kind, record.title, record.printer_id, record.printer,
                          record.copies, record.pages, record.sha256, record.bytes, record.spooler_id, record.detail,
                          at, at ))
            db.execute( "INSERT INTO print_events VALUES ( ?,?,?,? )", ( record.job_id, at, record.state.value, record.detail ))
            db.execute( "COMMIT" )
        finally:
            db.close()
        return self.get( record.job_id ), True   # type: ignore[return-value]

    def move( self, job_id: str, state: JobState, detail: str = "", expect: frozenset[ JobState ] | None = None,
              **fields: object ) -> JobRecord:
        """Change a job's state. With `expect`, only from one of those states (compare-and-set), else StaleState."""
        unknown = set( fields ) - set( FIELDS )
        if unknown:
            raise ValueError( f"not a job field: { sorted( unknown )}" )
        at   = self._now().isoformat()
        sets = [ "state = ?", "detail = ?", "updated_at = ?" ] + [ f"{ name } = ?" for name in fields ]
        args: list[ object ] = [ state.value, detail[ :500 ], at, *fields.values()]
        if state is JobState.SUBMITTING:
            sets.append( "submitted_at = ?" )
            args.append( at )
        db = self._db()
        try:
            db.execute( "BEGIN IMMEDIATE" )
            where, keys = "job_id = ?", [ job_id ]
            if expect is not None:
                where += f" AND state IN ( { ', '.join( '?' for _ in expect )} )"
                keys  += [ s.value for s in expect ]
            done = db.execute( f"UPDATE print_jobs SET { ', '.join( sets )} WHERE { where }", ( *args, *keys )).rowcount
            if done != 1:
                db.execute( "ROLLBACK" )
                if expect is not None and self.get( job_id ) is not None:
                    raise StaleState( f"job { job_id } is no longer { '/'.join( sorted( s.value for s in expect ))}" )
                raise KeyError( job_id )
            db.execute( "INSERT INTO print_events VALUES ( ?,?,?,? )", ( job_id, at, state.value, detail[ :500 ]))
            db.execute( "COMMIT" )
        finally:
            db.close()
        return self.get( job_id )   # type: ignore[return-value]

    def recent( self, limit: int = 20 ) -> list[ JobRecord ]:
        db = self._db()
        try:
            rows = db.execute( "SELECT * FROM print_jobs ORDER BY created_at DESC LIMIT ?", ( max( 1, min( limit, 100 )), )).fetchall()
        finally:
            db.close()
        return [ self._record( row ) for row in rows ]

    def submitted_since( self, seconds: float ) -> int:
        since = ( self._now() - timedelta( seconds=seconds )).isoformat()
        db = self._db()
        try:
            return int( db.execute( "SELECT COUNT(*) FROM print_jobs WHERE submitted_at IS NOT NULL AND submitted_at >= ?",
                                    ( since, )).fetchone()[ 0 ])
        finally:
            db.close()

    # -- recovery ------------------------------------------------------------------

    def recover( self ) -> list[ str ]:
        """Call only while holding the print lock, so no live run owns these rows.

        A job left in `submitting` -- the process died between handing pages to
        the spooler and hearing back -- becomes `ambiguous`. One left in
        `accepted` or `queued` never reached the spooler, so it becomes `failed`.
        Returns the ids it changed.
        """
        db = self._db()
        try:
            rows = db.execute( "SELECT job_id, state FROM print_jobs WHERE state IN ( ?, ?, ? )",
                               ( JobState.SUBMITTING.value, JobState.ACCEPTED.value, JobState.QUEUED.value )).fetchall()
        finally:
            db.close()
        for job_id, state in rows:
            if state == JobState.SUBMITTING.value:
                self.move( job_id, JobState.AMBIGUOUS, expect=frozenset({ JobState.SUBMITTING }), detail=
                           "the worker stopped while handing this job to the spooler; it may or may not have printed. "
                           "It will not be reprinted automatically -- check the printer, then start a new job if needed." )
            else:
                self.move( job_id, JobState.FAILED, "the worker stopped before anything was sent to the printer",
                           expect=frozenset({ JobState.ACCEPTED, JobState.QUEUED }))
        return [ row[ 0 ] for row in rows ]

    @staticmethod
    def _record( row: sqlite3.Row ) -> JobRecord:
        return JobRecord.model_validate( dict( row ))

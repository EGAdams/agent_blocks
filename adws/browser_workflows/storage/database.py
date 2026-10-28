"""The SQLite file and its schema. Nothing here knows what a recording means."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path( __file__ ).resolve().parents[ 2 ] / "adw_data" / "browser_workflows" / "click_workspace_sequences.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    start_url   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recording_steps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id      INTEGER NOT NULL REFERENCES recordings( id ) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    action_type       TEXT NOT NULL,
    url               TEXT NOT NULL DEFAULT '',
    value             TEXT,
    value_is_secret   INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    wait_after_json   TEXT NOT NULL DEFAULT '[]',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    recorded_at       TEXT NOT NULL,
    UNIQUE ( recording_id, position )
);
CREATE TABLE IF NOT EXISTS step_locators (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id  INTEGER NOT NULL REFERENCES recording_steps( id ) ON DELETE CASCADE,
    rank     INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    value    TEXT NOT NULL,
    tag      TEXT NOT NULL DEFAULT '',
    source   TEXT NOT NULL DEFAULT 'css'
);
CREATE TABLE IF NOT EXISTS recording_sessions (
    id           TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    tab_id       INTEGER,
    start_url    TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL DEFAULT '',
    ended_at     TEXT NOT NULL DEFAULT '',
    recording_id INTEGER REFERENCES recordings( id ) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS captured_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES recording_sessions( id ) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    event_json  TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playback_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id   INTEGER REFERENCES recordings( id ) ON DELETE SET NULL,
    recording_name TEXT NOT NULL DEFAULT '',
    actor          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    ended_at       TEXT NOT NULL DEFAULT '',
    message        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS playback_step_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES playback_runs( id ) ON DELETE CASCADE,
    step_id          INTEGER NOT NULL,
    position         INTEGER NOT NULL,
    action_type      TEXT NOT NULL,
    status           TEXT NOT NULL,
    strategy         TEXT NOT NULL DEFAULT '',
    message          TEXT NOT NULL DEFAULT '',
    diagnostics_json TEXT,
    started_at       TEXT NOT NULL,
    ended_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locator_repairs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id    INTEGER NOT NULL REFERENCES recording_steps( id ) ON DELETE CASCADE,
    run_id     INTEGER,
    strategy   TEXT NOT NULL,
    value      TEXT NOT NULL,
    tag        TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'repair',
    verified   INTEGER NOT NULL DEFAULT 0,
    applied    INTEGER NOT NULL DEFAULT 0,
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_steps_recording ON recording_steps( recording_id, position );
CREATE INDEX IF NOT EXISTS ix_locators_step   ON step_locators( step_id, rank );
CREATE INDEX IF NOT EXISTS ix_events_session  ON captured_events( session_id, seq );
CREATE INDEX IF NOT EXISTS ix_results_run     ON playback_step_results( run_id, position );
"""


class SqliteDatabase:
    """Opens the file (creating it and its schema the first time) and hands
    out short-lived connections, one transaction each."""

    def __init__( self, path: Path = DEFAULT_DB_PATH ) -> None:
        self._path = Path( path )
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with self.connect() as conn:
            conn.executescript( SCHEMA )

    @property
    def path( self ) -> Path:
        return self._path

    @contextmanager
    def connect( self ) -> Iterator[ sqlite3.Connection ]:
        conn = sqlite3.connect( self._path, timeout=10 )
        conn.row_factory = sqlite3.Row
        conn.execute( "PRAGMA foreign_keys = ON" )
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

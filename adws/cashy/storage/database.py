"""Cashy's tables, beside the recorder's in click_workspace_sequences.db, so
one file holds every executable sequence, its versions and its history."""
from __future__ import annotations

from browser_workflows.storage.database import SqliteDatabase

CASHY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cashy_sessions (
    id          TEXT PRIMARY KEY,
    body        TEXT NOT NULL,                -- a sanitised CashySession
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cashy_workflows (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bank         TEXT NOT NULL,
    goal         TEXT NOT NULL,
    version      INTEGER NOT NULL,
    playback     TEXT NOT NULL,               -- recordings.name
    status       TEXT NOT NULL DEFAULT 'candidate',
    start_url    TEXT NOT NULL DEFAULT '',
    needs        TEXT NOT NULL DEFAULT '[]',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    verified_run INTEGER NOT NULL DEFAULT 0,
    UNIQUE ( bank, goal, version )
);
CREATE TABLE IF NOT EXISTS cashy_downloads (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    bank     TEXT NOT NULL,
    account  TEXT NOT NULL DEFAULT '',
    period   TEXT NOT NULL DEFAULT '',
    path     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    sha256   TEXT NOT NULL,
    at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cashy_account_banks (
    account  TEXT PRIMARY KEY,                -- masked, "•6285"
    bank     TEXT NOT NULL
);
"""


def cashy_database( database: SqliteDatabase ) -> SqliteDatabase:
    with database.connect() as conn:
        conn.executescript( CASHY_SCHEMA )
    return database

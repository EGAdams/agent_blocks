"""IRecordingRepository over SQLite: recordings, their steps, their locators."""
from __future__ import annotations

import json
import sqlite3

from ..errors import DuplicateName, NotFound
from ..models import LocatorCandidate, RecordedAction, Recording, RecordingStep, RecordingSummary, WaitCondition, utc_now
from .database import SqliteDatabase


class SqliteRecordingRepository:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    # -- reading -------------------------------------------------------------

    def summaries( self ) -> list[ RecordingSummary ]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT r.id, r.name, r.start_url, r.updated_at, COUNT( s.id ) AS step_count "
                "FROM recordings r LEFT JOIN recording_steps s ON s.recording_id = r.id "
                "GROUP BY r.id ORDER BY r.name COLLATE NOCASE" ).fetchall()
        return [ RecordingSummary( id=r[ "id" ], name=r[ "name" ], step_count=r[ "step_count" ],
                                   start_url=r[ "start_url" ], updated_at=r[ "updated_at" ]) for r in rows ]

    def by_name( self, name: str ) -> Recording | None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT id FROM recordings WHERE name = ? COLLATE NOCASE", ( name.strip(), )).fetchone()
        return self.by_id( row[ "id" ]) if row else None

    def by_id( self, recording_id: int ) -> Recording | None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT * FROM recordings WHERE id = ?", ( recording_id, )).fetchone()
            if row is None:
                return None
            steps = [ self._step( conn, s ) for s in conn.execute(
                "SELECT * FROM recording_steps WHERE recording_id = ? ORDER BY position", ( recording_id, ))]
        return Recording( id=row[ "id" ], name=row[ "name" ], description=row[ "description" ],
                          start_url=row[ "start_url" ], created_at=row[ "created_at" ],
                          updated_at=row[ "updated_at" ], steps=steps )

    def step( self, step_id: int ) -> tuple[ int, RecordingStep ] | None:
        """A step by its id, with the id of the recording it belongs to."""
        with self._db.connect() as conn:
            row = conn.execute( "SELECT * FROM recording_steps WHERE id = ?", ( step_id, )).fetchone()
            return ( row[ "recording_id" ], self._step( conn, row )) if row else None

    # -- writing -------------------------------------------------------------

    def create( self, name: str, actions: list[ RecordedAction ], description: str = "" ) -> Recording:
        name = name.strip()
        if not name:
            raise ValueError( "a playback needs a name" )
        now = utc_now()
        try:
            with self._db.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO recordings ( name, description, start_url, created_at, updated_at ) VALUES ( ?, ?, ?, ?, ? )",
                    ( name, description, _start_url( actions ), now, now ))
                recording_id = int( cur.lastrowid )
                for position, action in enumerate( actions, start=1 ):
                    self._insert_step( conn, recording_id, position, action )
        except sqlite3.IntegrityError as exc:
            if "recordings.name" in str( exc ):
                raise DuplicateName( f"a playback named '{ name }' already exists" ) from exc
            raise
        return self._require( recording_id )

    def rename( self, recording_id: int, name: str ) -> None:
        name = name.strip()
        if not name:
            raise ValueError( "a playback needs a name" )
        try:
            with self._db.connect() as conn:
                cur = conn.execute( "UPDATE recordings SET name = ?, updated_at = ? WHERE id = ?",
                                    ( name, utc_now(), recording_id ))
        except sqlite3.IntegrityError as exc:
            raise DuplicateName( f"a playback named '{ name }' already exists" ) from exc
        if cur.rowcount == 0:
            raise NotFound( f"no playback #{ recording_id }" )

    def delete( self, recording_id: int ) -> None:
        with self._db.connect() as conn:
            cur = conn.execute( "DELETE FROM recordings WHERE id = ?", ( recording_id, ))
        if cur.rowcount == 0:
            raise NotFound( f"no playback #{ recording_id }" )

    def replace_steps( self, recording_id: int, steps: list[ RecordedAction ]) -> Recording:
        """The recording's steps become exactly `steps`, in that order. A
        `RecordingStep` keeps its id (so its history and repairs stay attached);
        a plain `RecordedAction` is inserted; any old step not listed is removed."""
        self._require( recording_id )
        with self._db.connect() as conn:
            kept = { s.id for s in steps if isinstance( s, RecordingStep )}
            existing = { r[ "id" ] for r in conn.execute( "SELECT id FROM recording_steps WHERE recording_id = ?", ( recording_id, ))}
            for gone in existing - kept:
                conn.execute( "DELETE FROM recording_steps WHERE id = ?", ( gone, ))
            # Positions are unique, so park the survivors out of the way first.
            conn.execute( "UPDATE recording_steps SET position = -position - 1000000 WHERE recording_id = ?", ( recording_id, ))
            for position, step in enumerate( steps, start=1 ):
                if isinstance( step, RecordingStep ) and step.id in existing:
                    self._write_step( conn, step.model_copy( update={ "position": position }))
                else:
                    self._insert_step( conn, recording_id, position, step )
            conn.execute( "UPDATE recordings SET start_url = ?, updated_at = ? WHERE id = ?",
                          ( _start_url( steps ), utc_now(), recording_id ))
        return self._require( recording_id )

    def update_step( self, step: RecordingStep ) -> None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT recording_id FROM recording_steps WHERE id = ?", ( step.id, )).fetchone()
            if row is None:
                raise NotFound( f"no step #{ step.id }" )
            self._write_step( conn, step )
            conn.execute( "UPDATE recordings SET updated_at = ? WHERE id = ?", ( utc_now(), row[ "recording_id" ]))

    # -- rows ----------------------------------------------------------------

    def _require( self, recording_id: int ) -> Recording:
        recording = self.by_id( recording_id )
        if recording is None:
            raise NotFound( f"no playback #{ recording_id }" )
        return recording

    @staticmethod
    def _insert_step( conn: sqlite3.Connection, recording_id: int, position: int, action: RecordedAction ) -> None:
        cur = conn.execute(
            "INSERT INTO recording_steps ( recording_id, position, action_type, url, value, value_is_secret, "
            "requires_approval, wait_after_json, metadata_json, recorded_at ) VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ?, ? )",
            ( recording_id, position, *_step_columns( action )))
        _write_locators( conn, int( cur.lastrowid ), action.locators )

    @staticmethod
    def _write_step( conn: sqlite3.Connection, step: RecordingStep ) -> None:
        conn.execute(
            "UPDATE recording_steps SET position = ?, action_type = ?, url = ?, value = ?, value_is_secret = ?, "
            "requires_approval = ?, wait_after_json = ?, metadata_json = ?, recorded_at = ? WHERE id = ?",
            ( step.position, *_step_columns( step ), step.id ))
        conn.execute( "DELETE FROM step_locators WHERE step_id = ?", ( step.id, ))
        _write_locators( conn, step.id, step.locators )

    @staticmethod
    def _step( conn: sqlite3.Connection, row: sqlite3.Row ) -> RecordingStep:
        locators = [ LocatorCandidate( strategy=l[ "strategy" ], value=l[ "value" ], tag=l[ "tag" ],
                                       source=l[ "source" ], rank=l[ "rank" ])
                     for l in conn.execute( "SELECT * FROM step_locators WHERE step_id = ? ORDER BY rank, id", ( row[ "id" ], ))]
        return RecordingStep(
            id                = row[ "id" ],
            position          = row[ "position" ],
            action_type       = row[ "action_type" ],
            url               = row[ "url" ],
            locators          = locators,
            value             = row[ "value" ],
            value_is_secret   = bool( row[ "value_is_secret" ]),
            requires_approval = bool( row[ "requires_approval" ]),
            wait_after        = [ WaitCondition( **w ) for w in json.loads( row[ "wait_after_json" ])],
            metadata          = json.loads( row[ "metadata_json" ]),
            recorded_at       = row[ "recorded_at" ],
        )


def _step_columns( action: RecordedAction ) -> tuple:
    # A secret's value is never written, whatever the caller handed in.
    value = None if action.value_is_secret else action.value
    return ( action.action_type, action.url, value, int( action.value_is_secret ), int( action.requires_approval ),
             json.dumps([ w.model_dump() for w in action.wait_after ]), json.dumps( action.metadata ), action.recorded_at )


def _write_locators( conn: sqlite3.Connection, step_id: int, locators: list[ LocatorCandidate ]) -> None:
    conn.executemany(
        "INSERT INTO step_locators ( step_id, rank, strategy, value, tag, source ) VALUES ( ?, ?, ?, ?, ?, ? )",
        [( step_id, l.rank, l.strategy, l.value, l.tag, l.source ) for l in locators ])


def _start_url( actions: list[ RecordedAction ]) -> str:
    return next(( a.url for a in actions if a.action_type == "navigate" and a.url ), "" )

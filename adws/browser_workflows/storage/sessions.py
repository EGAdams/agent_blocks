"""IRecordingSessionStore over SQLite: a session and its raw events, durable
from the first click, so a crash before "Save As?" loses nothing."""
from __future__ import annotations

from ..models import RawBrowserEvent, RecordingSessionInfo, utc_now
from .database import SqliteDatabase


class SqliteRecordingSessionStore:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    def open( self, info: RecordingSessionInfo ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO recording_sessions ( id, state, tab_id, start_url, title, started_at, ended_at, recording_id ) "
                "VALUES ( ?, ?, ?, ?, ?, ?, ?, ? )",
                ( info.id, info.state, info.tab_id, info.start_url, info.title,
                  info.started_at or utc_now(), info.ended_at, info.recording_id ))

    def update( self, info: RecordingSessionInfo ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recording_sessions SET state = ?, tab_id = ?, start_url = ?, title = ?, ended_at = ?, "
                "recording_id = ? WHERE id = ?",
                ( info.state, info.tab_id, info.start_url, info.title, info.ended_at, info.recording_id, info.id ))

    def append( self, session_id: str, event: RawBrowserEvent ) -> int:
        with self._db.connect() as conn:
            seq = conn.execute( "SELECT COALESCE( MAX( seq ), 0 ) + 1 FROM captured_events WHERE session_id = ?",
                                ( session_id, )).fetchone()[ 0 ]
            conn.execute( "INSERT INTO captured_events ( session_id, seq, event_json, received_at ) VALUES ( ?, ?, ?, ? )",
                          ( session_id, seq, event.model_dump_json(), utc_now() ))
        return int( seq )

    def events( self, session_id: str ) -> list[ RawBrowserEvent ]:
        with self._db.connect() as conn:
            rows = conn.execute( "SELECT event_json FROM captured_events WHERE session_id = ? ORDER BY seq",
                                 ( session_id, )).fetchall()
        return [ RawBrowserEvent.model_validate_json( r[ "event_json" ]) for r in rows ]

    def get( self, session_id: str ) -> RecordingSessionInfo | None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT * FROM recording_sessions WHERE id = ?", ( session_id, )).fetchone()
            count = conn.execute( "SELECT COUNT(*) FROM captured_events WHERE session_id = ?", ( session_id, )).fetchone()[ 0 ]
        return _info( row, count ) if row else None

    def latest_open( self ) -> RecordingSessionInfo | None:
        """The newest session still recording or waiting for a name."""
        with self._db.connect() as conn:
            row = conn.execute( "SELECT id FROM recording_sessions WHERE state IN ( 'recording', 'saving' ) "
                                "ORDER BY started_at DESC, rowid DESC LIMIT 1" ).fetchone()
        return self.get( row[ "id" ]) if row else None


def _info( row, count: int ) -> RecordingSessionInfo:
    return RecordingSessionInfo( id=row[ "id" ], state=row[ "state" ], tab_id=row[ "tab_id" ],
                                 start_url=row[ "start_url" ], title=row[ "title" ], started_at=row[ "started_at" ],
                                 ended_at=row[ "ended_at" ], event_count=count, recording_id=row[ "recording_id" ])

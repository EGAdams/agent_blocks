"""SqliteSessionStore: ISessionStore. A session is saved only through the sanitiser."""
from __future__ import annotations

from browser_workflows.storage.database import SqliteDatabase

from ..interfaces.session import ISessionSanitizer
from ..models import CashySession, utc_now


class SqliteSessionStore:
    def __init__( self, database: SqliteDatabase, sanitizer: ISessionSanitizer ) -> None:
        self._db        = database
        self._sanitizer = sanitizer

    def load( self, session_id: str ) -> CashySession:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT body FROM cashy_sessions WHERE id = ?", ( session_id, )).fetchone()
        return CashySession.model_validate_json( row[ "body" ]) if row else CashySession( id=session_id )

    def save( self, session: CashySession ) -> None:
        clean = self._sanitizer.clean( session.model_copy( update={ "updated_at": utc_now()}))
        with self._db.connect() as conn:
            conn.execute( "INSERT INTO cashy_sessions ( id, body, updated_at ) VALUES ( ?, ?, ? ) "
                          "ON CONFLICT ( id ) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
                          ( clean.id, clean.model_dump_json(), clean.updated_at ))

    def ids( self ) -> list[ str ]:
        with self._db.connect() as conn:
            return [ r[ "id" ] for r in conn.execute( "SELECT id FROM cashy_sessions ORDER BY updated_at DESC" )]

"""SqliteWorkflowRegistry: IWorkflowRegistry. Versions are only ever added;
an old one is retired by status, so the last working one is always there."""
from __future__ import annotations

import json

from browser_workflows.storage.database import SqliteDatabase

from ..models import WorkflowVersion


class SqliteWorkflowRegistry:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    def latest( self, bank: str, goal: str, verified_only: bool = True ) -> WorkflowVersion | None:
        where = "bank = ? AND goal = ? AND status " + ( "= 'verified'" if verified_only else "!= 'retired'" )
        with self._db.connect() as conn:
            row = conn.execute( f"SELECT * FROM cashy_workflows WHERE { where } ORDER BY version DESC LIMIT 1",
                                ( bank, goal )).fetchone()
        return _version( row ) if row else None

    def versions( self, bank: str = "", goal: str = "" ) -> list[ WorkflowVersion ]:
        with self._db.connect() as conn:
            rows = conn.execute( "SELECT * FROM cashy_workflows WHERE ( ? = '' OR bank = ? ) AND ( ? = '' OR goal = ? ) "
                                 "ORDER BY bank, goal, version", ( bank, bank, goal, goal )).fetchall()
        return [ _version( r ) for r in rows ]

    def add( self, version: WorkflowVersion ) -> WorkflowVersion:
        with self._db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO cashy_workflows ( bank, goal, version, playback, status, start_url, needs, note, created_at ) "
                "VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ? )",
                ( version.bank, version.goal, version.version, version.playback, version.status, version.start_url,
                  json.dumps( version.needs ), version.note, version.created_at ))
        return version.model_copy( update={ "id": cursor.lastrowid })

    def set_status( self, version_id: int, status: str, run_id: int = 0 ) -> None:
        with self._db.connect() as conn:
            conn.execute( "UPDATE cashy_workflows SET status = ?, verified_run = CASE WHEN ? > 0 THEN ? ELSE verified_run END "
                          "WHERE id = ?", ( status, run_id, run_id, version_id ))

    def next_version( self, bank: str, goal: str ) -> int:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT MAX( version ) AS v FROM cashy_workflows WHERE bank = ? AND goal = ?",
                                ( bank, goal )).fetchone()
        return int( row[ "v" ] or 0 ) + 1


def _version( row ) -> WorkflowVersion:
    data = dict( row )
    data[ "needs" ] = json.loads( data[ "needs" ] or "[]" )
    return WorkflowVersion.model_validate( data )

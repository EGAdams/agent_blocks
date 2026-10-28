"""IPlaybackRunLog over SQLite: every run, and every step's outcome in it."""
from __future__ import annotations

from ..models import PlaybackRun, StepDiagnostics, StepResult, utc_now
from .database import SqliteDatabase


class SqlitePlaybackRunLog:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    def start( self, recording_id: int, actor: str ) -> int:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT name FROM recordings WHERE id = ?", ( recording_id, )).fetchone()
            cur = conn.execute( "INSERT INTO playback_runs ( recording_id, recording_name, actor, status, started_at ) "
                                "VALUES ( ?, ?, ?, 'playing', ? )",
                                ( recording_id, row[ "name" ] if row else "", actor, utc_now() ))
        return int( cur.lastrowid )

    def record( self, result: StepResult ) -> None:
        diagnostics = result.diagnostics.model_dump_json() if result.diagnostics else None
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO playback_step_results ( run_id, step_id, position, action_type, status, strategy, message, "
                "diagnostics_json, started_at, ended_at ) VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ?, ? )",
                ( result.run_id, result.step_id, result.position, result.action_type, result.status, result.strategy,
                  result.message, diagnostics, result.started_at, result.ended_at ))

    def finish( self, run_id: int, status: str, message: str ) -> None:
        with self._db.connect() as conn:
            conn.execute( "UPDATE playback_runs SET status = ?, message = ?, ended_at = ? WHERE id = ?",
                          ( status, message, utc_now(), run_id ))

    def run( self, run_id: int ) -> PlaybackRun | None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT * FROM playback_runs WHERE id = ?", ( run_id, )).fetchone()
            if row is None:
                return None
            results = [ _result( r ) for r in conn.execute(
                "SELECT * FROM playback_step_results WHERE run_id = ? ORDER BY id", ( run_id, ))]
        return _run( row, results )

    def recent( self, limit: int = 20, recording_id: int | None = None ) -> list[ PlaybackRun ]:
        sql, args = "SELECT * FROM playback_runs", []
        if recording_id is not None:
            sql, args = sql + " WHERE recording_id = ?", [ recording_id ]
        with self._db.connect() as conn:
            rows = conn.execute( sql + " ORDER BY id DESC LIMIT ?", ( *args, limit )).fetchall()
        return [ _run( r, []) for r in rows ]


def _run( row, results: list[ StepResult ]) -> PlaybackRun:
    return PlaybackRun( id=row[ "id" ], recording_id=row[ "recording_id" ] or 0, name=row[ "recording_name" ],
                        actor=row[ "actor" ], status=row[ "status" ], started_at=row[ "started_at" ],
                        ended_at=row[ "ended_at" ], message=row[ "message" ], results=results )


def _result( row ) -> StepResult:
    diagnostics = StepDiagnostics.model_validate_json( row[ "diagnostics_json" ]) if row[ "diagnostics_json" ] else None
    return StepResult( run_id=row[ "run_id" ], step_id=row[ "step_id" ], position=row[ "position" ],
                       action_type=row[ "action_type" ], status=row[ "status" ], strategy=row[ "strategy" ],
                       message=row[ "message" ], diagnostics=diagnostics, started_at=row[ "started_at" ],
                       ended_at=row[ "ended_at" ])

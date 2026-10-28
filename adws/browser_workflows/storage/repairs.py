"""ILocatorRepairStore over SQLite: proposed locators, kept apart from the
steps until a person applies one."""
from __future__ import annotations

from ..errors import NotFound
from ..models import LocatorCandidate, RepairProposal, utc_now
from .database import SqliteDatabase


class SqliteLocatorRepairStore:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    def propose( self, proposal: RepairProposal ) -> RepairProposal:
        c = proposal.candidate
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO locator_repairs ( step_id, run_id, strategy, value, tag, source, verified, applied, reason, created_at ) "
                "VALUES ( ?, ?, ?, ?, ?, ?, ?, 0, ?, ? )",
                ( proposal.step_id, proposal.run_id, c.strategy, c.value, c.tag, c.source,
                  int( proposal.verified ), proposal.reason, utc_now() ))
        return proposal.model_copy( update={ "id": int( cur.lastrowid ), "applied": False })

    def get( self, repair_id: int ) -> RepairProposal | None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT * FROM locator_repairs WHERE id = ?", ( repair_id, )).fetchone()
        if row is None:
            return None
        return RepairProposal( id=row[ "id" ], step_id=row[ "step_id" ], run_id=row[ "run_id" ],
                               candidate=LocatorCandidate( strategy=row[ "strategy" ], value=row[ "value" ],
                                                           tag=row[ "tag" ], source=row[ "source" ], rank=0 ),
                               verified=bool( row[ "verified" ]), applied=bool( row[ "applied" ]), reason=row[ "reason" ])

    def mark_applied( self, repair_id: int ) -> None:
        with self._db.connect() as conn:
            cur = conn.execute( "UPDATE locator_repairs SET applied = 1 WHERE id = ?", ( repair_id, ))
        if cur.rowcount == 0:
            raise NotFound( f"no repair #{ repair_id }" )

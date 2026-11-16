"""Where a target's cycles are kept: the RSI framework's SQLite tables, and one JSON record per cycle.

    <data_dir>/<target>/improvement.sqlite3      revisions, artifacts, proposals, experiments, approvals,
                                                 activation records, snapshots -- the framework's own schema
    <data_dir>/<target>/cycles/<cycle_id>/       cycle.json, candidate.diff, gate_result.json, report.md
    <data_dir>/<target>/slots/<cycle_id>.pid     one file per cycle holding a running slot

Each target has its own database, so a Sydney pilot can never write into Mazda's live evidence
(`~/.mazda/self_improvement.sqlite3`), which this package does not open.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import rsi
from .models import CycleRecord, now


class RsiStore:
    """The framework's repositories over one database file. Facade: the engine asks this for a repository."""

    def __init__( self, db_path: Path ):
        db_path.parent.mkdir( parents=True, exist_ok=True )
        self.db_path = str( db_path )
        rsi.SqliteSchemaManager( self.db_path ).create_schema()
        self.revisions    = rsi.SqliteWrapperRevisionRepository( self.db_path )
        self.artifacts    = rsi.SqlitePromptArtifactRepository( self.db_path )
        self.traces       = rsi.SqliteTraceRepository( self.db_path )
        self.improvements = rsi.SqliteImprovementRepository( self.db_path )
        self.approvals    = rsi.SqliteHumanApprovalGateway( self.db_path )
        self.snapshots    = rsi.SqliteSnapshotStore( self.db_path, self.revisions )
        self.rollbacks    = rsi.RollbackService( self.revisions, self.snapshots )

    def has_revision( self, agent: str, revision: str ) -> bool:
        try:
            self.revisions.get_revision( agent, revision )
        except KeyError:
            return False
        return True

    def body( self, agent: str, revision: str ) -> str:
        return self.artifacts.get_artifact( agent, revision ).body


class FileCycleStore:
    """Implements ICycleStore. Writes are whole-file and atomic, so a killed run leaves the last good record."""

    def __init__( self, root: Path ):
        self._root  = root
        self._slots = root / "slots"

    def directory( self, cycle_id: str ) -> Path:
        path = self._root / "cycles" / cycle_id
        path.mkdir( parents=True, exist_ok=True )
        return path

    def save( self, cycle: CycleRecord ) -> None:
        cycle.updated_at = now()
        path = self.directory( cycle.cycle_id ) / "cycle.json"
        temp = path.with_suffix( ".json.tmp" )
        temp.write_text( cycle.model_dump_json( indent=1 ), encoding="utf-8" )
        os.replace( temp, path )

    def load( self, cycle_id: str ) -> CycleRecord:
        path = self._root / "cycles" / cycle_id / "cycle.json"
        if not path.is_file():
            raise KeyError( f"no cycle { cycle_id }" )
        return CycleRecord.model_validate_json( path.read_text( encoding="utf-8" ))

    def all( self ) -> list[ CycleRecord ]:
        found = [ CycleRecord.model_validate_json( p.read_text( encoding="utf-8" ))
                  for p in sorted(( self._root / "cycles" ).glob( "*/cycle.json" ))]
        return sorted( found, key=lambda c: c.started_at )

    def by_request_key( self, key: str ) -> CycleRecord | None:
        return next(( c for c in self.all() if key and c.request_key == key ), None )

    def acquire( self, cycle_id: str, limit: int ) -> bool:
        self._slots.mkdir( parents=True, exist_ok=True )
        held = 0
        for slot in self._slots.glob( "*.pid" ):
            if slot.stem == cycle_id:
                if _alive( slot ) and slot.read_text( encoding="utf-8" ).strip() != str( os.getpid()):
                    return False                    # this very cycle is being run by another live process
                continue
            if _alive( slot ):
                held += 1
            else:
                slot.unlink( missing_ok=True )          # its process is gone: the slot was never released
        if held >= limit:
            return False
        ( self._slots / f"{ cycle_id }.pid" ).write_text( str( os.getpid()), encoding="utf-8" )
        return True

    def release( self, cycle_id: str ) -> None:
        ( self._slots / f"{ cycle_id }.pid" ).unlink( missing_ok=True )


def _alive( slot: Path ) -> bool:
    try:
        os.kill( int( slot.read_text( encoding="utf-8" ).strip()), 0 )
    except ( ValueError, ProcessLookupError, OSError ):
        return False
    return True

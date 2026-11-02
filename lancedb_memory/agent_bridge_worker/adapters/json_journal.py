"""IWriteJournal as JSON files in a private folder on this machine (mode 0700).

    tasks/<task_id>.json      what a write task started from, and how it ended
    patches/<task_id>.patch   the task's full diff, kept even when the result carries less
    unsettled/<ws>.json       a post-office task that may still be running in that workspace

Every write is atomic (a temp file, then rename), so a crash leaves the old
record or the new one, never half of one.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class JsonWriteJournal:
    def __init__( self, root: Path ) -> None:
        self._root = Path( root )

    def load( self, task_id: str ) -> dict | None:
        path = self._dir( "tasks" ) / f"{ task_id }.json"
        try:
            return json.loads( path.read_text( encoding="utf-8" ))
        except FileNotFoundError:
            return None

    def save( self, task_id: str, record: dict ) -> None:
        self._write( self._dir( "tasks" ) / f"{ task_id }.json", json.dumps( record, indent=2 ))

    def save_patch( self, task_id: str, patch: str ) -> str:
        path = self._dir( "patches" ) / f"{ task_id }.patch"
        self._write( path, patch )
        return str( path )

    def unsettled( self, workspace: str ) -> str | None:
        path = self._dir( "unsettled" ) / f"{ workspace }.json"
        try:
            return str( json.loads( path.read_text( encoding="utf-8" ))[ "local_task_id" ])
        except ( FileNotFoundError, KeyError, ValueError ):
            return None

    def mark_unsettled( self, workspace: str, local_task_id: str ) -> None:
        self._write( self._dir( "unsettled" ) / f"{ workspace }.json", json.dumps({ "local_task_id": local_task_id }))

    def clear_unsettled( self, workspace: str ) -> None:
        ( self._dir( "unsettled" ) / f"{ workspace }.json" ).unlink( missing_ok=True )

    def _dir( self, name: str ) -> Path:
        path = self._root / name
        path.mkdir( parents=True, exist_ok=True, mode=0o700 )
        os.chmod( self._root, 0o700 )
        return path

    @staticmethod
    def _write( path: Path, text: str ) -> None:
        fd, tmp = tempfile.mkstemp( dir=path.parent, prefix=".tmp_" )
        try:
            with os.fdopen( fd, "w", encoding="utf-8" ) as out:
                out.write( text )
            os.replace( tmp, path )
        except BaseException:
            Path( tmp ).unlink( missing_ok=True )
            raise

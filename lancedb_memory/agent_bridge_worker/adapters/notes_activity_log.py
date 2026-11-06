"""INotesActivity as an append-only JSONL file, shared with the dashboard.

Same file and line shape as `letta-code/dashboard/notes_activity.py`
(`NOTES_ACTIVITY_LOG`, default `~/.cache/agent_bridge/notes_activity.jsonl`),
with `"source": "worker"`, so `activity_snapshot` shows both sides of one
Bridge task -- the dashboard's read/write/conflict/auth events and this
agent's refusals and verification -- on one Worker Activity row. A line never
holds note text, and logging never breaks a task: write errors are swallowed.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

FIELDS = ( "task_id", "op", "filename", "revision", "expected_revision", "detail" )


class JsonlNotesActivity:
    def __init__( self, path: Path ) -> None:
        self._path = Path( path )

    def __repr__( self ) -> str:
        return f"JsonlNotesActivity({ str( self._path )!r })"

    def record( self, event: str, **fields: str ) -> None:
        entry = { "at": datetime.datetime.now( datetime.timezone.utc ).isoformat( timespec="seconds" ),
                  "source": "worker", "event": event }
        for key in FIELDS:
            value = str( fields.get( key ) or "" )
            if value:
                entry[ key ] = value[ :240 ]
        try:
            self._path.parent.mkdir( parents=True, exist_ok=True )
            fd = os.open( self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600 )
            with os.fdopen( fd, "a", encoding="utf-8" ) as handle:
                handle.write( json.dumps( entry ) + "\n" )
        except OSError:
            pass

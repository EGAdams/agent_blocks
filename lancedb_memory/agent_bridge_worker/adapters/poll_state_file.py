"""IPollObserver that keeps the poller's current state in one small JSON file.

The dashboard's Poll Settings tab reads it to draw the countdown. Written
atomically (temp file + rename) so a reader never sees half a file. It holds
times and counts only -- never a task id, prompt or token.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..watch import PollState

DEFAULT_PATH = Path.home() / ".cache" / "agent_bridge" / "poll_state.json"


class JsonPollStateFile:
    def __init__( self, path: Path = DEFAULT_PATH ) -> None:
        self._path = path

    def changed( self, state: PollState ) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        temp = self._path.with_suffix( ".tmp" )
        stamped = state.model_copy( update={ "pid": os.getpid()})
        temp.write_text( stamped.model_dump_json() + "\n", encoding="utf-8" )
        os.replace( temp, self._path )

    def read( self ) -> dict[ str, Any ] | None:
        """IPollStateReader: the last state, or None when there is none yet or it is unreadable."""
        try:
            data = json.loads( self._path.read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            return None
        return data if isinstance( data, dict ) else None

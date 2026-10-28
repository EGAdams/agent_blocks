"""IPlaybackControl: run, pause or stop, asked between steps.

`FilePlaybackControl` keeps one small file per run, so a second process (an
ADW, the menu, an agent) can pause or stop a run it did not start.
"""
from __future__ import annotations

from pathlib import Path

COMMANDS = ( "run", "pause", "stop" )


class FilePlaybackControl:
    def __init__( self, directory: Path ) -> None:
        self._dir = Path( directory )

    def command( self, run_id: int ) -> str:
        try:
            text = ( self._dir / f"run_{ run_id }.control" ).read_text().strip()
        except OSError:
            return "run"
        return text if text in COMMANDS else "run"

    def set( self, run_id: int, command: str ) -> None:
        if command not in COMMANDS:
            raise ValueError( f"control command must be one of { COMMANDS }" )
        self._dir.mkdir( parents=True, exist_ok=True )
        ( self._dir / f"run_{ run_id }.control" ).write_text( command )


class MemoryPlaybackControl:
    def __init__( self ) -> None:
        self._commands: dict[ int, str ] = {}

    def command( self, run_id: int ) -> str:
        return self._commands.get( run_id, "run" )

    def set( self, run_id: int, command: str ) -> None:
        self._commands[ run_id ] = command

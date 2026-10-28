"""IMessageSink: a file a menu or receptionist reads, or its Null Object.

The file's one line is a contract with whoever reads it -- the same rule as
adw_map_open.py and adw_calculate_distance.py.
"""
from __future__ import annotations

from pathlib import Path

from .interfaces import IMessageSink


class FileMessageSink:
    def __init__( self, path: str ):
        self._path = Path( path )

    def deliver( self, message: str ) -> None:
        self._path.write_text( message + "\n" )


class NullMessageSink:
    def deliver( self, message: str ) -> None:
        pass


class PathMessageSinkFactory:
    def for_path( self, path: str ) -> IMessageSink:
        return FileMessageSink( path ) if path else NullMessageSink()

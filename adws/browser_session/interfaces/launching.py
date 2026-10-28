"""Starting the debuggable Chrome: what to run, where its profile lives,
how the process is spawned, and the launcher that composes those."""
from __future__ import annotations

from typing import Protocol

from ..models import ChromeLaunchResult, DebugEndpoint


class IChromeCommandBuilder( Protocol ):
    """Builder: assembles a chrome.exe command line one part at a time.
    Immutable -- every step returns a new builder, so a configured base can
    be shared and extended per launch without either seeing the other."""

    def executable( self, path: str ) -> "IChromeCommandBuilder": ...
    def debugging_port( self, port: int ) -> "IChromeCommandBuilder": ...
    def profile( self, directory: str ) -> "IChromeCommandBuilder": ...
    def flag( self, flag: str ) -> "IChromeCommandBuilder": ...
    def url( self, url: str ) -> "IChromeCommandBuilder": ...
    def build( self ) -> list[ str ]: ...


class IProcessSpawner( Protocol ):
    """Starts a process without waiting for it. Raises if it cannot start."""

    def spawn( self, argv: list[ str ]) -> None: ...


class IProfileDirResolver( Protocol ):
    """Where the debug Chrome keeps its own profile."""

    def resolve( self ) -> str: ...


class IChromeLauncher( Protocol ):
    def launch( self, url: str, endpoint: DebugEndpoint ) -> ChromeLaunchResult: ...

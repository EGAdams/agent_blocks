"""IChromeLauncher: base command + port + URL, handed to a spawner.
Never blocks, never raises -- the same two promises map_open.py keeps.

Launching again with the same profile while it is open only adds a tab to
the running window, which makes the launch idempotent.
"""
from __future__ import annotations

from .interfaces import IChromeCommandBuilder, IProcessSpawner
from .models import ChromeLaunchResult, DebugEndpoint

WINDOWS_CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"


class DebugChromeLauncher:

    def __init__( self, base: IChromeCommandBuilder, spawner: IProcessSpawner ):
        self._base    = base
        self._spawner = spawner

    def launch( self, url: str, endpoint: DebugEndpoint ) -> ChromeLaunchResult:
        try:
            command = self._base.debugging_port( endpoint.port ).url( url ).build()
            self._spawner.spawn( command )
        except Exception as e:
            return ChromeLaunchResult( launched=False, reason=f"Could not start Chrome: { e }" )
        return ChromeLaunchResult( launched=True, command=command )

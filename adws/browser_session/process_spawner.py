"""IProcessSpawner: start a process detached, never wait on it."""
from __future__ import annotations

import subprocess


class DetachedProcessSpawner:

    def __init__( self, cwd: str = "/mnt/c" ):
        # A Windows .exe started from a Linux-only directory warns about
        # UNC paths; starting it from /mnt/c keeps it quiet.
        self._cwd = cwd

    def spawn( self, argv: list[ str ]) -> None:
        subprocess.Popen( argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True, cwd=self._cwd )

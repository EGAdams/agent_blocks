"""IWorkspaceLock with flock: one write task per workspace, across worker processes.

The lock lives with the journal, outside the workspace, and the kernel drops it
if the worker dies, so a crash never leaves a workspace locked.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from ..ports import WriteBlocked


class FlockWorkspaceLock:
    def __init__( self, folder: Path ) -> None:
        self._folder = Path( folder )
        self._held: dict[ str, int ] = {}

    def acquire( self, workspace: str ) -> None:
        self._folder.mkdir( parents=True, exist_ok=True, mode=0o700 )
        fd = os.open( self._folder / f"{ workspace }.lock", os.O_RDWR | os.O_CREAT, 0o600 )
        try:
            fcntl.flock( fd, fcntl.LOCK_EX | fcntl.LOCK_NB )
        except BlockingIOError:
            os.close( fd )
            raise WriteBlocked( f"another write task is working in { workspace } right now" ) from None
        self._held[ workspace ] = fd

    def release( self, workspace: str ) -> None:
        fd = self._held.pop( workspace, None )
        if fd is not None:
            fcntl.flock( fd, fcntl.LOCK_UN )
            os.close( fd )

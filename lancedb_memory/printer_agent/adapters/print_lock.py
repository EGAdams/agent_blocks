"""One print at a time on this machine: an exclusive, non-blocking flock."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from ..errors import PrinterBusy


class FlockPrintLock:
    def __init__( self, path: Path ) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire( self ) -> None:
        self._path.parent.mkdir( mode=0o700, parents=True, exist_ok=True )
        fd = os.open( self._path, os.O_RDWR | os.O_CREAT, 0o600 )
        try:
            fcntl.flock( fd, fcntl.LOCK_EX | fcntl.LOCK_NB )
        except BlockingIOError:
            os.close( fd )
            raise PrinterBusy( "another print job is running on this machine; try again when it finishes" ) from None
        self._fd = fd

    def release( self ) -> None:
        if self._fd is not None:
            fcntl.flock( self._fd, fcntl.LOCK_UN )
            os.close( self._fd )
            self._fd = None

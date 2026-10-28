"""ITaskLiveness implementations.

`FileLockLiveness` answers "is anybody still working on this task?" with an
exclusive `flock` the worker holds for as long as it executes. The kernel
drops the lock when the worker's process dies -- however it dies, SIGKILL
included -- so a task whose lock can be taken has nobody behind it. No pid
files, no heartbeats, nothing to go stale.
"""
from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class FileLockLiveness:

    def __init__( self, root: Path ) -> None:
        self._root = Path( root )

    @contextmanager
    def hold( self, task_id: str ) -> Iterator[ None ]:
        self._root.mkdir( parents=True, exist_ok=True )
        with open( self._path( task_id ), "a" ) as handle:
            fcntl.flock( handle, fcntl.LOCK_EX )
            try:
                yield
            finally:
                fcntl.flock( handle, fcntl.LOCK_UN )
        try:
            os.unlink( self._path( task_id ))
        except FileNotFoundError:
            pass

    def is_alive( self, task_id: str ) -> bool:
        path = self._path( task_id )
        if not path.exists():
            return False
        with open( path, "a" ) as handle:
            try:
                fcntl.flock( handle, fcntl.LOCK_EX | fcntl.LOCK_NB )
            except BlockingIOError:
                return True
            fcntl.flock( handle, fcntl.LOCK_UN )
            return False

    def _path( self, task_id: str ) -> Path:
        return self._root / f"{ task_id }.lock"


class NoLiveness:
    """Nobody holds anything: every claimed task looks abandoned. For callers that never sweep."""

    @contextmanager
    def hold( self, task_id: str ) -> Iterator[ None ]:
        yield

    def is_alive( self, task_id: str ) -> bool:
        return False

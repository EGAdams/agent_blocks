"""IWorkerLauncher and IClock implementations.

`DetachedWorkerLauncher` is how asynchronous delegation stays asynchronous:
it spawns one ADW run (`adws/adw_op_execute_task.py --agent <name>`) that
drains that agent's inbox and exits, and it returns at once -- the same
never-block posture as `adw_modules/map_open.py`. Each spawn is a visible
ADW session, so every task's execution is observable in `just obs`.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


class DetachedWorkerLauncher:

    def __init__( self, script: Path, cwd: Path, python: str | None = None, log_dir: Path | None = None,
                  skip: frozenset[ str ] = frozenset()) -> None:
        self._script  = Path( script )
        self._cwd     = Path( cwd )
        self._python  = python or sys.executable
        self._log_dir = Path( log_dir ) if log_dir else None
        self._skip    = skip            # agents nobody should work for, e.g. the post office itself
        self.errors: list[ str ] = []

    def wake( self, agent: str ) -> None:
        if agent in self._skip:
            return
        log = subprocess.DEVNULL
        if self._log_dir is not None:
            self._log_dir.mkdir( parents=True, exist_ok=True )
            log = open( self._log_dir / f"{ agent }.log", "a" )
        try:
            subprocess.Popen( [ self._python, str( self._script ), "--agent", agent ],
                              cwd=self._cwd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                              start_new_session=True )
        except OSError as exc:
            # Never raise: the task is safely queued, and the next wake drains it.
            self.errors.append( f"{ agent }: { exc }" )


class RecordingLauncher:
    """Remembers who was woken and does nothing else. Tests drive the workers."""

    def __init__( self ) -> None:
        self.woken: list[ str ] = []

    def wake( self, agent: str ) -> None:
        self.woken.append( agent )


class SystemClock:

    def monotonic( self ) -> float:
        return time.monotonic()

    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )

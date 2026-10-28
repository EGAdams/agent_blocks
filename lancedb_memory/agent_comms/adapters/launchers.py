"""IWorkerLauncher and IClock implementations.

`DetachedWorkerLauncher` is how asynchronous delegation stays asynchronous:
it spawns one ADW run (`adws/adw_op_execute_task.py --agent <name>`) that
drains that agent's inbox and exits, and it returns at once -- the same
never-block posture as `adw_modules/map_open.py`. Each spawn is a visible
ADW session, so every task's execution is observable in `just obs`.

`outside_unit_prefix()` is what keeps that worker alive when the process
that woke it is restarted. A receptionist sidecar runs as a systemd user
unit, and `start_new_session` does not leave the unit's cgroup -- so
`systemctl --user restart sydney-receptionist` used to kill Opus halfway
through Sydney's plan and leave the task "in progress" forever. Run under
`systemd-run --user --scope`, the worker gets a cgroup of its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


class DetachedWorkerLauncher:

    def __init__( self, script: Path, cwd: Path, python: str | None = None, log_dir: Path | None = None,
                  skip: frozenset[ str ] = frozenset(), prefix: list[ str ] | None = None ) -> None:
        self._prefix  = list( prefix or [])
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
            subprocess.Popen( [ *self._prefix, self._python, str( self._script ), "--agent", agent ],
                              cwd=self._cwd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                              start_new_session=True )
        except OSError as exc:
            # Never raise: the task is safely queued, and the next wake drains it.
            self.errors.append( f"{ agent }: { exc }" )


def outside_unit_prefix() -> list[ str ]:
    """`systemd-run --user --scope` when there is a user manager to ask, else nothing."""
    if shutil.which( "systemd-run" ) and os.environ.get( "XDG_RUNTIME_DIR" ) \
            and Path( os.environ[ "XDG_RUNTIME_DIR" ], "systemd" ).exists():
        return [ "systemd-run", "--user", "--scope", "--quiet", "--collect", "--" ]
    return []


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

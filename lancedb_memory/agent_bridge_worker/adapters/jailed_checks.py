"""ICheckRunner: one configured check, run in a jail with no network and a read-only workspace.

The argv comes from workspaces.json, never from a task, and is passed as a list
(no shell). `wrap` is the jail -- BwrapJail.wrap in production -- injected so
this adapter never names one.
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..models import CheckRun

TAIL = 2000


class JailedCheckRunner:
    def __init__( self, wrap: Callable[[ list[ str ], Path ], list[ str ]], timeout_seconds: float = 300.0 ) -> None:
        self._wrap, self._timeout = wrap, timeout_seconds

    def run( self, workdir: Path, command: list[ str ]) -> CheckRun:
        start = time.monotonic()
        try:
            argv = self._wrap( command, workdir )
        except Exception as error:                      # noqa: BLE001 -- JailUnavailable and friends
            return CheckRun( command=command, exit_code=None, passed=False, seconds=0.0,
                             output_tail=f"not run: the check jail is unavailable ({ error })" )
        try:
            done = subprocess.run( argv, capture_output=True, text=True, timeout=self._timeout, cwd=workdir )
        except subprocess.TimeoutExpired:
            return CheckRun( command=command, exit_code=None, passed=False, seconds=round( time.monotonic() - start, 1 ),
                             output_tail=f"timed out after { round( self._timeout ) } seconds" )
        output = ( done.stdout or "" ) + ( done.stderr or "" )
        return CheckRun( command=command, exit_code=done.returncode, passed=done.returncode == 0,
                         seconds=round( time.monotonic() - start, 1 ), output_tail=output[ -TAIL: ])

"""IServiceLauncher: the recorder service is up, or is started detached and
waited for (by asking it, not by sleeping a fixed time)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from browser_session.interfaces.system import IClock

from ..interfaces.presenting import IRecorderClient


class DetachedServiceLauncher:
    def __init__( self, client: IRecorderClient, command: list[ str ], cwd: Path, log_path: Path, clock: IClock,
                  timeout_seconds: float = 20.0 ) -> None:
        self._client  = client
        self._command = command
        self._cwd     = cwd
        self._log     = log_path
        self._clock   = clock
        self._timeout = timeout_seconds

    def ensure_running( self ) -> str:
        if self._client.status() is not None:
            return ""
        self._log.parent.mkdir( parents=True, exist_ok=True )
        try:
            with open( self._log, "ab" ) as out:
                subprocess.Popen( self._command, cwd=self._cwd, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                  start_new_session=True )
        except OSError as exc:
            return f"could not start the recorder service ({ exc })"
        deadline = self._clock.now() + self._timeout
        while self._clock.now() < deadline:
            self._clock.sleep( 0.5 )
            if self._client.status() is not None:
                return ""
        return f"the recorder service did not answer within { self._timeout:g}s; see { self._log }"

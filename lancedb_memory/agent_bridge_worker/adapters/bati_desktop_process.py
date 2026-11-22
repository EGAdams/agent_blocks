"""IBatiCommands over `adws/adw_bati_chrome.py`, run with `uv run` as a child process.

The operation comes from a fixed table; every value is passed as `--flag=value`. The ADW writes its Outcome to a
private temp file (`--json-out`), and it writes one even when it fails or times out. Running and cleanup are
Cashy's `RunningCashyCommand`, unchanged: the shape is the same, only the ADW differs.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from ..bati_desktop_agent import READS, WRITES
from ..cashy_agent import CashyCommandFailed
from .cashy_process import RunningCashyCommand

ADW = "adw_bati_chrome.py"


class UvBatiCommands:
    def __init__( self, uv: Path, adws: Path, cwd: Path, hard_limit: float = 170.0 ) -> None:
        self._uv, self._adws, self._cwd, self._limit = uv, adws, cwd, hard_limit

    def argv( self, operation: str, flags: dict[ str, str ], result: str ) -> list[ str ]:
        if operation not in READS | WRITES:
            raise CashyCommandFailed( f"no such BATI desktop operation { operation }" )
        argv = [ str( self._uv ), "run", "--quiet", str( self._adws / ADW ), operation ]
        for name, value in sorted( flags.items()):
            argv.append( f"--{ name }={ value }" )
        return argv + [ f"--json-out={ result }" ]

    def start( self, operation: str, flags: dict[ str, str ]) -> RunningCashyCommand:
        fd_out, out = tempfile.mkstemp( prefix="bati_out_" )
        fd_err, err = tempfile.mkstemp( prefix="bati_err_" )
        fd_res, res = tempfile.mkstemp( prefix="bati_res_" )
        os.close( fd_res )
        try:
            # A new session: a Chrome the run launches must outlive this run and a worker restart.
            process = subprocess.Popen( self.argv( operation, flags, res ), cwd=self._cwd, stdin=subprocess.DEVNULL,
                                        stdout=fd_out, stderr=fd_err, env={ **os.environ, "PYTHONUNBUFFERED": "1" },
                                        start_new_session=True )
        except OSError as exc:
            for fd in ( fd_out, fd_err ):
                os.close( fd )
            for path in ( out, err, res ):
                Path( path ).unlink( missing_ok=True )
            raise CashyCommandFailed( f"could not start uv ({ type( exc ).__name__ })" ) from None
        os.close( fd_out )
        os.close( fd_err )
        return RunningCashyCommand( process, Path( out ), Path( err ), Path( res ), self._limit, time.monotonic())

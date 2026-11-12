"""IScalaStorageCommands over `adws/adw_scala_storage.py`, run with `uv run` as a child process.

The same process handling as the Deployment ADW's adapter (`RunningDeployCommand`): private temp files for output
and the outcome, a new session so a worker restart cannot take a transfer down with it, and `--flag=value`
arguments so a value is never read as an option.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from ..deploy_agent import DeployCommandFailed
from ..scala_storage_agent import READS, WRITES
from .deploy_process import RunningDeployCommand


class UvScalaStorageCommands:
    def __init__( self, uv: Path, adw: Path, cwd: Path, hard_limit: float = 1200.0 ) -> None:
        self._uv, self._adw, self._cwd, self._limit = uv, adw, cwd, hard_limit

    def argv( self, operation: str, flags: dict[ str, str ], result: str ) -> list[ str ]:
        if operation not in READS | WRITES:
            raise DeployCommandFailed( f"no such storage operation { operation }" )
        argv = [ str( self._uv ), "run", "--quiet", str( self._adw ), f"storage.{ operation }" ]
        argv += [ f"--{ name }={ value }" for name, value in sorted( flags.items())]
        return argv + [ f"--json-out={ result }" ]

    def start( self, operation: str, flags: dict[ str, str ]) -> RunningDeployCommand:
        fd_out, out = tempfile.mkstemp( prefix="scala_storage_out_" )
        fd_err, err = tempfile.mkstemp( prefix="scala_storage_err_" )
        fd_res, res = tempfile.mkstemp( prefix="scala_storage_res_" )
        os.close( fd_res )
        try:
            process = subprocess.Popen( self.argv( operation, flags, res ), cwd=self._cwd, stdin=subprocess.DEVNULL,
                                        stdout=fd_out, stderr=fd_err, env={ **os.environ, "PYTHONUNBUFFERED": "1" },
                                        start_new_session=True )
        except OSError as exc:
            for fd in ( fd_out, fd_err ):
                os.close( fd )
            for path in ( out, err, res ):
                Path( path ).unlink( missing_ok=True )
            raise DeployCommandFailed( f"could not start uv ({ type( exc ).__name__ })" ) from None
        os.close( fd_out )
        os.close( fd_err )
        return RunningDeployCommand( process, Path( out ), Path( err ), Path( res ), self._limit, time.monotonic())

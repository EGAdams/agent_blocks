"""IImprovementCommands over `adws/adw_improvement_cycle.py` and `adws/adw_improvement_ops.py`, run as a child
process with the interpreter that can import the target's runner (lancedb_memory's venv). Same process handling as
the machine supervisor adapter: private temp files for output and the outcome, a new session, and `--flag=value`
arguments so a value is never read as an option. A flag whose value is "" is a bare switch (`--approve`).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from ..deploy_agent import DeployCommandFailed
from ..improvement_agent import READS, WRITES
from .deploy_process import RunningDeployCommand


class ProcessImprovementCommands:
    def __init__( self, python: Path, cycle_adw: Path, ops_adw: Path, cwd: Path, hard_limit: float = 5700.0,
                  env: dict[ str, str ] | None = None ) -> None:
        self._python, self._cycle, self._ops, self._cwd, self._limit = python, cycle_adw, ops_adw, cwd, hard_limit
        self._env = dict( env or {})

    def argv( self, operation: str, flags: dict[ str, str ], result: str ) -> list[ str ]:
        if operation == "start":
            argv = [ str( self._python ), str( self._cycle )]
        elif operation in READS or operation in WRITES:
            argv = [ str( self._python ), str( self._ops ), operation ]
        else:
            raise DeployCommandFailed( f"no such improvement operation { operation }" )
        argv += [ f"--{ name }" if value == "" else f"--{ name }={ value }" for name, value in sorted( flags.items())]
        return argv + [ f"--json-out={ result }" ]

    def start( self, operation: str, flags: dict[ str, str ]) -> RunningDeployCommand:
        fd_out, out = tempfile.mkstemp( prefix="improvement_out_" )
        fd_err, err = tempfile.mkstemp( prefix="improvement_err_" )
        fd_res, res = tempfile.mkstemp( prefix="improvement_res_" )
        os.close( fd_res )
        try:
            process = subprocess.Popen( self.argv( operation, flags, res ), cwd=self._cwd, stdin=subprocess.DEVNULL,
                                        stdout=fd_out, stderr=fd_err, env={ **os.environ, **self._env, "PYTHONUNBUFFERED": "1" },
                                        start_new_session=True )
        except OSError as exc:
            for fd in ( fd_out, fd_err ):
                os.close( fd )
            for path in ( out, err, res ):
                Path( path ).unlink( missing_ok=True )
            raise DeployCommandFailed( f"could not start the improvement ADW ({ type( exc ).__name__ })" ) from None
        os.close( fd_out )
        os.close( fd_err )
        return RunningDeployCommand( process, Path( out ), Path( err ), Path( res ), self._limit, time.monotonic())

"""IDeployCommands over `adws/adw_deploy.py`, run with `uv run` as a child process.

The operation comes from a fixed list; every value is passed as `--flag=value`, so a value that starts with a dash is
still just a value. The ADW writes its outcome to a private temp file (`--json-out`) and prints its ADW id, which is
read back and added to the answer. Output goes to temp files too, so a chatty run cannot stall.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from ..deploy_agent import READS, WRITES, DeployCommandFailed

ADW_ID = re.compile( r"adw_id: ([0-9a-f]{8})" )


class RunningDeployCommand:
    def __init__( self, process: subprocess.Popen, out: Path, err: Path, result: Path, hard_limit: float, started: float ) -> None:
        self._p, self._out, self._err, self._result = process, out, err, result
        self._limit, self._started = hard_limit, started

    def poll( self ) -> dict | None:
        code = self._p.poll()
        if code is None:
            if time.monotonic() - self._started > self._limit:
                self.stop()
                raise DeployCommandFailed( "the deployment workflow hung and was stopped" )
            return None
        try:
            log    = self._out.read_text( encoding="utf-8", errors="replace" )
            answer = json.loads( self._result.read_text( encoding="utf-8" )) if self._result.stat().st_size else None
        except ( OSError, ValueError ):
            answer = None
        finally:
            self._cleanup()
        if not isinstance( answer, dict ):
            raise DeployCommandFailed( f"exit status { code }, no outcome file" )
        found = ADW_ID.search( log )
        return { **answer, "adw_id": found.group( 1 ) if found else None }

    def stop( self ) -> None:
        if self._p.poll() is None:
            self._p.kill()
            self._p.wait( timeout=10 )
        self._cleanup()

    def _cleanup( self ) -> None:
        for path in ( self._out, self._err, self._result ):
            path.unlink( missing_ok=True )


class UvDeployCommands:
    def __init__( self, uv: Path, adw: Path, cwd: Path, hard_limit: float = 1800.0 ) -> None:
        self._uv, self._adw, self._cwd, self._limit = uv, adw, cwd, hard_limit

    def argv( self, operation: str, flags: dict[ str, str ], dry_run: bool, result: str ) -> list[ str ]:
        if operation not in READS | WRITES:
            raise DeployCommandFailed( f"no such deployment operation { operation }" )
        argv = [ str( self._uv ), "run", "--quiet", str( self._adw ), f"deploy.{ operation }" ]
        argv += [ f"--{ name }={ value }" for name, value in sorted( flags.items())]
        if dry_run:
            argv.append( "--dry-run" )
        return argv + [ f"--json-out={ result }" ]

    def start( self, operation: str, flags: dict[ str, str ], dry_run: bool ) -> RunningDeployCommand:
        fd_out, out = tempfile.mkstemp( prefix="deploy_out_" )
        fd_err, err = tempfile.mkstemp( prefix="deploy_err_" )
        fd_res, res = tempfile.mkstemp( prefix="deploy_res_" )
        os.close( fd_res )
        try:
            # A new session: a worker restarted by its own deploy must not take the ADW down with it.
            process = subprocess.Popen( self.argv( operation, flags, dry_run, res ), cwd=self._cwd, stdin=subprocess.DEVNULL,
                                        stdout=fd_out, stderr=fd_err, env={ **os.environ, "PYTHONUNBUFFERED": "1" },
                                        start_new_session=True )
        except OSError as exc:
            for fd, path in (( fd_out, out ), ( fd_err, err )):
                os.close( fd )
            for path in ( out, err, res ):
                Path( path ).unlink( missing_ok=True )
            raise DeployCommandFailed( f"could not start uv ({ type( exc ).__name__ })" ) from None
        os.close( fd_out )
        os.close( fd_err )
        return RunningDeployCommand( process, Path( out ), Path( err ), Path( res ), self._limit, time.monotonic())

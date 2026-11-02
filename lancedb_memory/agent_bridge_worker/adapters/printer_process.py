"""IPrinterCommands over `adws/adw_printer.py`, run with `uv run` as a child process.

argv is fixed apart from the subcommand (from a fixed map) and a job id (already
matched against the task-id pattern); a print request goes on stdin. The ADW
prints lifecycle lines as it runs and ends with one `PRINTER_RESULT {json}` line,
which is the only line read back. Output goes to private temp files, so a
chatty run can never fill a pipe and stall.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..printer_agent import PrinterCommandFailed

MARKER      = "PRINTER_RESULT "
SUBCOMMANDS = frozenset({ "print", "cancel", "status", "recent", "discover" })
JOB_ID      = re.compile( r"t_[0-9a-f]{24}" )


class RunningPrinterCommand:
    def __init__( self, process: subprocess.Popen, out: Path, err: Path, hard_limit: float, started: float,
                  monotonic ) -> None:
        self._p, self._out, self._err = process, out, err
        self._limit, self._started, self._now = hard_limit, started, monotonic

    def poll( self ) -> dict | None:
        code = self._p.poll()
        if code is None:
            if self._now() - self._started > self._limit:   # past every timeout the ADW has: it is stuck
                self.stop()
                raise PrinterCommandFailed( "the printer workflow hung and was stopped" )
            return None
        try:
            lines = self._out.read_text( encoding="utf-8", errors="replace" ).splitlines()
        finally:
            self._cleanup()
        for line in reversed( lines ):
            if line.startswith( MARKER ):
                try:
                    answer = json.loads( line[ len( MARKER ): ])
                except ValueError:
                    break
                if isinstance( answer, dict ):
                    return answer
                break
        raise PrinterCommandFailed( f"exit status { code }, no result line" )

    def stop( self ) -> None:
        if self._p.poll() is None:
            self._p.kill()
            self._p.wait( timeout=10 )
        self._cleanup()

    def _cleanup( self ) -> None:
        for path in ( self._out, self._err ):
            path.unlink( missing_ok=True )


class UvPrinterCommands:
    def __init__( self, uv: Path, adw: Path, cwd: Path, hard_limit: float = 1800.0 ) -> None:
        self._uv, self._adw, self._cwd, self._limit = uv, adw, cwd, hard_limit

    def argv( self, subcommand: str, job_id: str | None ) -> list[ str ]:
        if subcommand not in SUBCOMMANDS:
            raise PrinterCommandFailed( f"no such printer command { subcommand }" )
        argv = [ str( self._uv ), "run", "--quiet", str( self._adw ), subcommand ]
        if job_id is not None:
            if not JOB_ID.fullmatch( job_id ):
                raise PrinterCommandFailed( "a job id is t_ and 24 hex digits" )
            argv += [ "--job-id", job_id ]
        return argv

    def start( self, subcommand: str, job_id: str | None, request: str | None ) -> RunningPrinterCommand:
        import time

        argv = self.argv( subcommand, job_id )
        fd_out, out = tempfile.mkstemp( prefix="printer_out_" )
        fd_err, err = tempfile.mkstemp( prefix="printer_err_" )
        try:
            process = subprocess.Popen( argv, cwd=self._cwd, stdin=subprocess.PIPE, stdout=fd_out, stderr=fd_err,
                                        env={ **os.environ, "PYTHONUNBUFFERED": "1" })
        except OSError as exc:
            for fd, path in (( fd_out, out ), ( fd_err, err )):
                os.close( fd )
                Path( path ).unlink( missing_ok=True )
            raise PrinterCommandFailed( f"could not start uv ({ type( exc ).__name__ })" ) from None
        os.close( fd_out )
        os.close( fd_err )
        try:
            process.stdin.write(( request or "" ).encode( "utf-8" ))    # type: ignore[union-attr]
            process.stdin.close()                                       # type: ignore[union-attr]
        except BrokenPipeError:
            pass
        return RunningPrinterCommand( process, Path( out ), Path( err ), self._limit, time.monotonic(), time.monotonic )

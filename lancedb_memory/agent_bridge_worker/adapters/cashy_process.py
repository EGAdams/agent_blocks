"""ICashyCommands over `adws/adw_cashy_<op>.py`, run with `uv run` as a child process; ITabTitles over loopback CDP.

The ADW comes from a fixed table; every value is passed as `--flag=value`, so a value that starts with a dash is still
just a value. The ADW writes its outcome to a private temp file (`--json-out`). The Chrome it drives is the debug
Chrome on this machine's loopback (port 9222): nothing here listens, and nothing reaches another machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from ..cashy_agent import ADWS, CashyCommandFailed

BOOLEAN = frozenset({ "launch_chrome" })


class RunningCashyCommand:
    def __init__( self, process: subprocess.Popen, out: Path, err: Path, result: Path, hard_limit: float, started: float ) -> None:
        self._p, self._out, self._err, self._result = process, out, err, result
        self._limit, self._started = hard_limit, started

    def poll( self ) -> dict | None:
        code = self._p.poll()
        if code is None:
            if time.monotonic() - self._started > self._limit:
                self.stop()
                raise CashyCommandFailed( "the Cashy workflow hung and was stopped" )
            return None
        try:
            answer = json.loads( self._result.read_text( encoding="utf-8" )) if self._result.stat().st_size else None
            tail   = self._err.read_text( encoding="utf-8", errors="replace" )[ -300: ]
        except ( OSError, ValueError ):
            answer, tail = None, ""
        finally:
            self._cleanup()
        if not isinstance( answer, dict ):
            raise CashyCommandFailed( f"exit status { code }, no outcome file{ ': ' + tail.strip().splitlines()[ -1 ] if tail.strip() else '' }" )
        return answer

    def stop( self ) -> None:
        if self._p.poll() is None:
            self._p.kill()
            self._p.wait( timeout=10 )
        self._cleanup()

    def _cleanup( self ) -> None:
        for path in ( self._out, self._err, self._result ):
            path.unlink( missing_ok=True )


class UvCashyCommands:
    def __init__( self, uv: Path, adws: Path, cwd: Path, port: int = 9222, hard_limit: float = 240.0 ) -> None:
        self._uv, self._adws, self._cwd, self._port, self._limit = uv, adws, cwd, port, hard_limit

    def argv( self, adw: str, flags: dict[ str, str ], result: str ) -> list[ str ]:
        if adw not in set( ADWS.values()):
            raise CashyCommandFailed( f"no such Cashy workflow { adw }" )
        argv = [ str( self._uv ), "run", "--quiet", str( self._adws / f"adw_cashy_{ adw }.py" )]
        for name, value in sorted( flags.items()):
            if name in BOOLEAN:
                if value == "true":
                    argv.append( "--" + name.replace( "_", "-" ))
            else:
                argv.append( f"--{ name.replace( '_', '-' )}={ value }" )
        return argv + [ f"--port={ self._port }", f"--json-out={ result }" ]

    def start( self, adw: str, flags: dict[ str, str ]) -> RunningCashyCommand:
        fd_out, out = tempfile.mkstemp( prefix="cashy_out_" )
        fd_err, err = tempfile.mkstemp( prefix="cashy_err_" )
        fd_res, res = tempfile.mkstemp( prefix="cashy_res_" )
        os.close( fd_res )
        try:
            # A new session: the Chrome a launch starts must outlive this run and a worker restart.
            process = subprocess.Popen( self.argv( adw, flags, res ), cwd=self._cwd, stdin=subprocess.DEVNULL,
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


class LoopbackTabTitles:
    """A tab's title from the debug Chrome's own target list, on 127.0.0.1 only."""

    def __init__( self, port: int = 9222, timeout_seconds: float = 3.0 ) -> None:
        self._url, self._timeout = f"http://127.0.0.1:{ port }/json/list", timeout_seconds

    def title( self, target_id: str ) -> str:
        with urllib.request.urlopen( self._url, timeout=self._timeout ) as response:   # noqa: S310 -- fixed loopback URL
            targets = json.loads( response.read().decode( "utf-8" ))
        return next(( str( t.get( "title" ) or "" ) for t in targets if t.get( "id" ) == target_id ), "" )

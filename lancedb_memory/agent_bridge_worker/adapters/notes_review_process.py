"""INotesReviewCommands over `adws/adw_notes_review.py`, run with `uv run` as a child process.

The operation comes from the fixed review table; every value is passed as `--flag=value`, so a value that starts with
a dash is still just a value, and a switch is passed only by its own name. The ADW writes its whole answer to a private
temp file (`--json-out`), with its ADW id inside (`stamp_adw_id`). The ADW reads the same Notes token file this worker
does, from its own default path, so no secret is put on a command line.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from ..notes_ops.review import SWITCHES, TABLE, VALUE_FLAGS, ReviewCommandFailed

PUBLIC  = frozenset( public for public, *_ in TABLE.values())
ALLOWED = frozenset( VALUE_FLAGS.values()) | { "note" }


class UvNotesReviewCommands:
    def __init__( self, uv: Path, adw: Path, cwd: Path ) -> None:
        self._uv, self._adw, self._cwd = uv, adw, cwd

    def argv( self, operation: str, flags: dict[ str, str ], switches: list[ str ], result: str ) -> list[ str ]:
        if operation not in PUBLIC:
            raise ReviewCommandFailed( f"no such review operation { operation }" )
        if set( flags ) - ALLOWED or set( switches ) - set( SWITCHES.values()):
            raise ReviewCommandFailed( "a flag outside the review table" )
        argv = [ str( self._uv ), "run", "--quiet", str( self._adw ), operation ]
        argv += [ f"--{ name }={ value }" for name, value in sorted( flags.items())]
        argv += [ f"--{ name }" for name in sorted( switches )]
        return argv + [ f"--json-out={ result }" ]

    def run( self, operation: str, flags: dict[ str, str ], switches: list[ str ], timeout: float,
             beat: Callable[[], str ] = lambda: "continue", stoppable: bool = True, poll: float = 2.0 ) -> dict:
        fd_res, result = tempfile.mkstemp( prefix="notes_review_" )
        fd_err, errors = tempfile.mkstemp( prefix="notes_review_err_" )
        os.close( fd_res )
        try:
            try:
                process = subprocess.Popen( self.argv( operation, flags, switches, result ), cwd=self._cwd,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=fd_err,
                                            env={ **os.environ, "PYTHONUNBUFFERED": "1" })
            except OSError as exc:
                raise ReviewCommandFailed( f"could not start uv ({ type( exc ).__name__ })" ) from None
            finally:
                os.close( fd_err )
            started = time.monotonic()
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    _kill( process )
                    raise ReviewCommandFailed( f"no answer within { int( timeout )}s" )
                if beat() != "continue" and stoppable:
                    _kill( process )
                    raise ReviewCommandFailed( "stopped: the task was cancelled or its lease was taken back" )
                time.sleep( poll )
            try:
                answer = json.loads( Path( result ).read_text( encoding="utf-8" ) or "null" )
            except ( OSError, ValueError ):
                answer = None
            if not isinstance( answer, dict ):
                tail = Path( errors ).read_text( encoding="utf-8", errors="replace" ).strip().splitlines()[ -1: ]
                raise ReviewCommandFailed( f"exit status { process.returncode }, no answer"
                                           + ( f": { tail[ 0 ][ :200 ]}" if tail else "" ))
            return answer
        finally:
            Path( result ).unlink( missing_ok=True )
            Path( errors ).unlink( missing_ok=True )


def _kill( process: subprocess.Popen ) -> None:
    process.kill()
    process.wait( timeout=10 )

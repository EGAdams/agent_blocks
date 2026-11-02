"""ICommandRunner over subprocess: an argv list, no shell, a hard timeout."""
from __future__ import annotations

import subprocess

from ..models import CommandOutput


class SubprocessRunner:
    def run( self, argv: list[ str ], timeout: float, stdin: bytes | None = None ) -> CommandOutput:
        try:
            done = subprocess.run( argv, input=stdin, capture_output=True, timeout=timeout, shell=False, check=False )
        except subprocess.TimeoutExpired:
            return CommandOutput( returncode=124, stderr=b"timed out" )
        except FileNotFoundError:
            return CommandOutput( returncode=127, stderr=b"program not found" )
        return CommandOutput( returncode=done.returncode, stdout=done.stdout, stderr=done.stderr )

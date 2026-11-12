"""IMachineTransport: run remote_helper.py on the destination -- in a local subprocess when this IS the
destination, otherwise as the command of the existing key-authenticated ssh connection. No credentials here:
`BatchMode=yes` means a missing key fails instead of prompting.

The helper's source and arguments travel base64-encoded, so no value is ever parsed by a shell as anything but
one word, and the scan's bytes travel on stdin.
"""
from __future__ import annotations

import base64
import json
import shlex
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .interfaces import StorageRefused, TargetUnreachable
from .models import StorageTarget

HELPER    = Path( __file__ ).with_name( "remote_helper.py" )
BOOTSTRAP = ( "import base64,sys;"
              "exec(compile(base64.b64decode(sys.argv[1]),'scala_storage_helper','exec'),"
              "{'__name__':'scala_storage_helper'})" )


def _b64( data: bytes ) -> str:
    return base64.b64encode( data ).decode( "ascii" )


class HelperTransport:
    """One machine. `argv_prefix` is either [python] or [ssh ..., host]; the helper is the same either way."""

    def __init__( self, target: StorageTarget, argv_prefix: list[ str ], remote: bool, timeout: float = 120.0 ) -> None:
        self._target, self._prefix, self._remote, self._timeout = target, argv_prefix, remote, timeout

    def call( self, op: str, args: dict[ str, Any ], data: bytes | None = None ) -> dict:
        payload = { "expected_hostname": self._target.hostname, "root": self._target.root,
                    "folders": list( self._target.folders ), **args }
        words = [ "-c", BOOTSTRAP, _b64( HELPER.read_bytes()), op, _b64( json.dumps( payload ).encode())]
        argv = ( self._prefix + [ "python3 " + " ".join( shlex.quote( w ) for w in words )] if self._remote
                 else self._prefix + words )
        try:
            done = subprocess.run( argv, input=data or b"", capture_output=True, timeout=self._timeout )
        except subprocess.TimeoutExpired:
            raise TargetUnreachable( f"{ self._target.machine } did not answer within { int( self._timeout )} s" ) from None
        except OSError as exc:
            raise TargetUnreachable( f"{ self._target.machine } could not be contacted ({ type( exc ).__name__ })" ) from None
        if self._remote and done.returncode == 255:
            reason = done.stderr.decode( "utf-8", "replace" ).strip().splitlines()[ -1:] or [ "no detail" ]
            raise TargetUnreachable( f"{ self._target.machine } ({ self._target.ssh.host if self._target.ssh else '?' }) "
                                     f"is unreachable: { reason[ 0 ][ :200 ]}" )
        lines = [ l for l in done.stdout.decode( "utf-8", "replace" ).splitlines() if l.startswith( "{" )]
        try:
            answer = json.loads( lines[ -1 ])
        except ( IndexError, ValueError ):
            raise TargetUnreachable( f"{ self._target.machine } gave no answer (exit { done.returncode }): "
                                     + done.stderr.decode( "utf-8", "replace" )[ -200:]) from None
        if not answer.get( "ok" ):
            extra = { k: v for k, v in answer.items() if k not in ( "ok", "error", "message" )}
            raise StorageRefused( str( answer.get( "error" )), str( answer.get( "message" )), **extra )
        return answer


class SshOrLocalTransports:
    """ITransportFactory: local when this host is the target, the configured ssh route otherwise."""

    def __init__( self, here: str | None = None, python: str | None = None, timeout: float = 120.0 ) -> None:
        self._here    = ( here or socket.gethostname()).lower()
        self._python  = python or sys.executable or "python3"
        self._timeout = timeout

    def for_target( self, target: StorageTarget ) -> HelperTransport:
        if target.hostname.lower() == self._here:
            return HelperTransport( target, [ self._python ], remote=False, timeout=self._timeout )
        if target.ssh is None:
            raise TargetUnreachable( f"{ target.machine } has no ssh route from { self._here }" )
        ssh = [ "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
                "-p", str( target.ssh.port ), f"{ target.ssh.user }@{ target.ssh.host }" ]
        return HelperTransport( target, ssh, remote=True, timeout=self._timeout )

"""IWorkerService over `systemctl --user` -- the worker's own unit, nothing else.

The unit name is fixed by the composition root, never taken from a request.
"""
from __future__ import annotations

import subprocess

from ..settings_editor import SettingsRejected, WorkerStatus


def parse_show( out: str ) -> WorkerStatus:
    """`systemctl show --timestamp=unix` key=value lines. Pure."""
    kv      = dict( line.split( "=", 1 ) for line in out.splitlines() if "=" in line )
    started = kv.get( "ActiveEnterTimestamp", "" ).lstrip( "@" )
    return WorkerStatus( active=kv.get( "ActiveState" ) or "unknown", pid=int( kv.get( "MainPID" ) or 0 ),
                         started_at=float( started ) if started.isdigit() and int( started ) else None )


class SystemdUserService:
    def __init__( self, unit: str ) -> None:
        self._unit = unit

    def _run( self, *args: str ) -> subprocess.CompletedProcess[ str ]:
        return subprocess.run([ "systemctl", "--user", *args, self._unit ],
                              capture_output=True, text=True, timeout=30 )

    def status( self ) -> WorkerStatus:
        try:
            out = self._run( "show", "--timestamp=unix", "-p", "ActiveState", "-p", "MainPID",
                             "-p", "ActiveEnterTimestamp" ).stdout
        except ( OSError, subprocess.SubprocessError ):
            return WorkerStatus()
        return parse_show( out )

    def restart( self ) -> None:
        try:
            done = self._run( "restart" )
        except ( OSError, subprocess.SubprocessError ) as error:
            raise SettingsRejected( f"The settings were saved, but the restart failed: { error }" ) from None
        if done.returncode != 0:
            why = ( done.stderr or done.stdout ).strip()[ :200 ]
            raise SettingsRejected( f"The settings were saved, but the restart failed: { why }" )

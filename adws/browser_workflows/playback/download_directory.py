"""Resolve a download folder on the machine running Chrome, including WSL.

Chrome receives the native Windows path, never WSL's /mnt/c equivalent.
An override is useful for redirected Downloads folders and remote Chrome.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

from ..errors import BrowserWorkflowError

DOWNLOAD_DIR_ENV = "BROWSER_WORKFLOW_DOWNLOAD_DIR"

#: Where cmd.exe is on any Windows install. A systemd user service inherits a plain PATH with none of
#: WSL's Windows entries, so `which` finds nothing there even though interop itself works -- which is
#: how a download from the Bridge worker failed while the same download from a shell succeeded.
CMD_EXE_PATHS = ( "/mnt/c/Windows/System32/cmd.exe", "/mnt/c/WINDOWS/system32/cmd.exe" )

#: WSL hands each login shell its own interop socket in WSL_INTEROP, and a Windows program cannot be
#: started without one. A systemd user service is not a login shell, so it has no such variable, and
#: cmd.exe there exits 1 -- the second half of the same Bridge-worker download failure. This is the
#: socket WSL's init keeps for the whole distro, so it is the right one to fall back to. A caller that
#: has `browser_control`'s WslInteropLink should ensure() first -- it probes every socket and can
#: recover when this one belongs to a dead session; this is the fallback for everyone else.
INTEROP_ENV   = "WSL_INTEROP"
INIT_INTEROP  = "/run/WSL/1_interop"


def interop_environment() -> dict[ str, str ]:
    """This process's environment, with an interop socket added if it is missing one."""
    environment = dict( os.environ )
    if not environment.get( INTEROP_ENV ) and Path( INIT_INTEROP ).exists():
        environment[ INTEROP_ENV ] = INIT_INTEROP
    return environment


def windows_shell() -> str:
    """cmd.exe's path, or "" if this machine has no Windows side to ask."""
    found = shutil.which( "cmd.exe" )
    if found:
        return found
    return next(( path for path in CMD_EXE_PATHS if Path( path ).exists()), "" )


class NativeDownloadDirectory:
    def __init__( self, settings_path: Path | None = None ) -> None:
        self._settings = settings_path

    def resolve( self ) -> str:
        override = os.environ.get( DOWNLOAD_DIR_ENV, "" ).strip()
        if not override and self._settings is not None and self._settings.exists():
            try:
                override = self._settings.read_text().strip()
            except OSError as exc:
                raise BrowserWorkflowError( f"cannot read download folder from { self._settings }: { exc }" ) from exc
        if override:
            if not ( Path( override ).is_absolute() or PureWindowsPath( override ).is_absolute() ):
                raise BrowserWorkflowError( f"download folder must be an absolute path on Chrome's machine ({ DOWNLOAD_DIR_ENV })" )
            return override
        shell = windows_shell()
        if shell:
            try:
                result = subprocess.run([ shell, "/c", "echo %USERPROFILE%" ], cwd="/mnt/c",
                                         env=interop_environment(),
                                         capture_output=True, text=True, timeout=15, check=True )
                profile = result.stdout.strip()
                if not PureWindowsPath( profile ).is_absolute() or "%" in profile:
                    raise ValueError( "Windows did not return an absolute user profile" )
            except ( OSError, subprocess.SubprocessError, ValueError ) as exc:
                raise BrowserWorkflowError( f"cannot find Windows Downloads; set { DOWNLOAD_DIR_ENV }: { exc }" ) from exc
            return str( PureWindowsPath( profile ) / "Downloads" / "BrowserRecorder" )
        if "microsoft" in os.uname().release.lower():
            raise BrowserWorkflowError( f"cannot reach Windows from WSL; set { DOWNLOAD_DIR_ENV } to a Windows folder" )
        return str( Path.home() / "Downloads" / "BrowserRecorder" )

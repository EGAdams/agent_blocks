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
        if shutil.which( "cmd.exe" ):
            try:
                result = subprocess.run([ "cmd.exe", "/c", "echo %USERPROFILE%" ], cwd="/mnt/c",
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

"""IProfileDirResolver: `%LOCALAPPDATA%\\<folder>` as a Windows path."""
from __future__ import annotations

import subprocess

PROFILE_FOLDER = "adw-chrome-debug"


class WindowsLocalAppDataProfile:
    """Asks cmd.exe for %LOCALAPPDATA% once; falls back to C:\\Users\\Public."""

    def __init__( self, folder: str = PROFILE_FOLDER ):
        self._folder = folder

    def resolve( self ) -> str:
        try:
            out = subprocess.run([ "cmd.exe", "/c", "echo %LOCALAPPDATA%" ], capture_output=True,
                                 text=True, timeout=15, cwd="/mnt/c" ).stdout.strip()
        except Exception:
            out = ""
        base = out if out and "%" not in out else r"C:\Users\Public"
        return f"{ base }\\{ self._folder }"

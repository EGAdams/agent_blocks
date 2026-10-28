"""Copy the recorder extension to a Windows folder Chrome can load unpacked.

Branded Chrome ignores --load-extension, so loading it is one manual step a
person does once (chrome://extensions -> Developer mode -> Load unpacked).
This puts the files somewhere Windows Chrome can see and says exactly what to
click. The copy is replaced whole, so a second run is an update."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from browser_session.interfaces import IProfileDirResolver

from ..extension_identity import extension_id
from .common import Operation, OperationOutcome, ok, refused

EXTENSION_DIR = Path( __file__ ).resolve().parents[ 1 ] / "chrome_extension"
INSTALL_FOLDER = "adw-browser-recorder"


def wsl_path( windows_path: str ) -> Path:
    return Path( subprocess.run([ "wslpath", "-u", windows_path ], capture_output=True, text=True,
                                timeout=15, check=True ).stdout.strip())


class InstallExtension( Operation ):
    name        = "install_extension"
    description = "Copy the recorder extension where Windows Chrome can load it unpacked"

    def __init__( self, target: IProfileDirResolver, to_local: Callable[[ str ], Path ] = wsl_path,
                  source: Path = EXTENSION_DIR ) -> None:
        self._target   = target
        self._to_local = to_local
        self._source   = source

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        windows = str( args.get( "to" ) or self._target.resolve())
        try:
            local = self._to_local( windows )
        except ( OSError, subprocess.SubprocessError ) as exc:
            return refused( f"cannot reach { windows } from WSL: { exc }" )
        if local.exists():
            shutil.rmtree( local )
        shutil.copytree( self._source, local, ignore=shutil.ignore_patterns( "*.md", "tests" ))
        return ok( f"copied to { windows }. In the debug Chrome: chrome://extensions -> Developer mode on -> "
                   f"Load unpacked -> { windows } (extension id { extension_id()}; Reload it after updates)",
                   windows_path=windows, extension_id=extension_id())

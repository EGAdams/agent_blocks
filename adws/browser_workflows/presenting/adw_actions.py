"""IAdwRunner and IWorkspaceActions over the `adw_browser_*.py` scripts.

The menu never calls an operation in-process: each choice is a real ADW run,
on this terminal (stdin and stdout inherited, so playback can ask "type YES"
and read a password with getpass), and its one line comes back through
--message-out."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..interfaces.presenting import IAdwRunner

ADWS_DIR = Path( __file__ ).resolve().parents[ 2 ]


class SubprocessAdwRunner:
    def __init__( self, command: list[ str ] | None = None, adws_dir: Path = ADWS_DIR ) -> None:
        self._command  = command or [ "uv", "run", "--quiet" ]
        self._adws_dir = adws_dir

    def run( self, adw: str, args: list[ str ]) -> tuple[ bool, str ]:
        script = self._adws_dir / f"adw_browser_{ adw }.py"
        with tempfile.TemporaryDirectory() as scratch:
            line_file = Path( scratch ) / "line.txt"
            try:
                done = subprocess.run([ *self._command, str( script ), *args, "--message-out", str( line_file )],
                                      cwd=self._adws_dir.parent )
            except OSError as exc:
                return False, f"could not run { script.name }: { exc }"
            line = line_file.read_text().strip() if line_file.exists() else ""
            return done.returncode == 0, line or f"{ script.name } exited { done.returncode } without a result"


class AdwWorkspaceActions:
    def __init__( self, runner: IAdwRunner ) -> None:
        self._runner = runner

    def start_recording( self ) -> tuple[ bool, str ]:
        return self._runner.run( "start_recording", [])

    def stop_recording( self ) -> tuple[ bool, str ]:
        return self._runner.run( "stop_recording", [])

    def save_recording( self, name: str ) -> tuple[ bool, str ]:
        return self._runner.run( "save_recording", [ "--name", name ])

    def discard_recording( self ) -> tuple[ bool, str ]:
        return self._runner.run( "discard_recording", [])

    def execute_playback( self, name: str ) -> tuple[ bool, str ]:
        return self._runner.run( "execute_saved_playback", [ "--name", name, "--interactive" ])

    def describe_playback( self, name: str ) -> tuple[ bool, str ]:
        return self._runner.run( "load_recording", [ "--name", name ])

    def edit_playback( self, name: str, edit: str, options: dict[ str, str ]) -> tuple[ bool, str ]:
        args = [ "--name", name, "--edit", edit ]
        for key, value in options.items():
            flag = "--" + key.replace( "_", "-" )
            args += [ flag ] if value == "" else [ flag, value ]        # "" marks a bare switch such as --yes
        return self._runner.run( "edit_playback", args )

    def playback_history( self, name: str = "", run_id: int = 0 ) -> tuple[ bool, str ]:
        args = ( [ "--run-id", str( run_id )] if run_id else []) + ([ "--name", name ] if name else [])
        return self._runner.run( "playback_history", args )

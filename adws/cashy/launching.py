"""Starting the debug Chrome when Cashy is asked to use it and it isn't up.

`LaunchingConnection` is a Decorator over any IBrowserConnection: an attach
that fails because nothing answers on the DevTools port starts the debug
Chrome (through adw_interface_browser_debug_open.py, which owns how that is
done and uses its own profile) and tries once more. Any other failure is
passed on as it was. The person's everyday Chrome is never touched.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from browser_workflows.errors import BrowserWorkflowError

from .interfaces.browser import IBrowserConnection, ICashyBrowser, IChromeLauncher
from .interfaces.desk import IDebugChromeProbe

DEBUG_OPEN_ADW = Path( __file__ ).resolve().parent.parent / "adw_interface_browser_debug_open.py"


#: The ADW opens http(s) only. Cashy navigates to the bank's own page next.
START_PAGE = "https://www.google.com/"


class AdwDebugChromeLauncher:
    def __init__( self, port: int = 9222, script: Path = DEBUG_OPEN_ADW, timeout: float = 90,
                  start_page: str = START_PAGE ) -> None:
        self._port    = port
        self._script  = script
        self._timeout = timeout
        self._start   = start_page

    def launch( self ) -> str:
        """The ADW's own one-line message (--message-out), not its log."""
        handle, message_file = tempfile.mkstemp( prefix="cashy-debug-open-", suffix=".txt" )
        Path( message_file ).unlink( missing_ok=True )
        command = [ sys.executable, str( self._script ), "Cashy needs the debug Chrome", "--url", self._start,
                    "--port", str( self._port ), "--message-out", message_file ]
        try:
            done = subprocess.run( command, capture_output=True, text=True, timeout=self._timeout,
                                   cwd=str( self._script.parent.parent ))
        except ( OSError, subprocess.TimeoutExpired ) as exc:
            return f"couldn't start the debug Chrome: { exc }"
        finally:
            os.close( handle )
        try:
            message = Path( message_file ).read_text().strip()
        except OSError:
            message = ""
        finally:
            Path( message_file ).unlink( missing_ok=True )
        return message[ :300 ] or f"debug-open exited { done.returncode }"


class LaunchingConnection:
    def __init__( self, inner: IBrowserConnection, probe: IDebugChromeProbe, launcher: IChromeLauncher,
                  wait_seconds: float = 20, sleep=time.sleep ) -> None:
        self._inner    = inner
        self._probe    = probe
        self._launcher = launcher
        self._wait     = wait_seconds
        self._sleep    = sleep
        self.last_launch = ""

    def attach( self, target_id: str, hosts: list[ str ]) -> ICashyBrowser:
        try:
            return self._inner.attach( target_id, hosts )
        except BrowserWorkflowError:
            if self._probe.reachable():
                raise                               # Chrome is up; the failure is something else
        self.last_launch = self._launcher.launch()
        waited = 0.0
        while not self._probe.reachable():
            if waited >= self._wait:
                raise BrowserWorkflowError( "the debug Chrome isn't answering and I couldn't start it "
                                            f"({ self.last_launch }). If it says 'Exec format error', "
                                            "WSL's Windows interop needs re-registering (sudo)." )
            self._sleep( 0.5 )
            waited += 0.5
        return self._inner.attach( target_id, hosts )

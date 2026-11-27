"""Where the everyday Chrome's browser websocket is: `DevToolsActivePort`, written by Chrome when
chrome://inspect/#remote-debugging is on. Line 1 is the port, line 2 the browser path (which acts as a key:
it is never logged). Re-read on every connect, because a restarted Chrome picks a new port."""
from __future__ import annotations

from pathlib import Path


class ChromeNotReachable( Exception ):
    """The everyday Chrome can't be spoken to; the message says what the person should do."""


class PortFileLocator:
    def __init__( self, port_file: Path ) -> None:
        self._file = port_file

    def __call__( self ) -> str:
        try:
            lines = self._file.read_text( encoding="utf-8" ).split()
        except OSError:
            raise ChromeNotReachable( "Chrome isn't open with remote debugging on (open Chrome; check "
                                      "chrome://inspect/#remote-debugging)" ) from None
        if len( lines ) < 2 or not lines[ 0 ].isdigit() or not lines[ 1 ].startswith( "/devtools/browser/" ):
            raise ChromeNotReachable( "Chrome's DevToolsActivePort file is not in the expected form" )
        return f"ws://127.0.0.1:{ int( lines[ 0 ]) }{ lines[ 1 ] }"

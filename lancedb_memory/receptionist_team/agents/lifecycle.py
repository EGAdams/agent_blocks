"""StdoutLifecycleLog -- an ILifecycleLog that works on this interpreter.

The interface, the mixin and the console sink all come from the SSSF template
(see ../../sssf_interfaces). This file exists for one reason: the template's
shipped `PrintLifecycleLog` and `ConsoleLifecycleLog` both format with
`f"#{ id( obj ):x }"`, and the bracket-spacing convention put a space *inside*
the format specifier -- `"x "` is not a valid spec, so calling either one
raises `ValueError: Invalid format specifier`. Worth reporting upstream; until
it is fixed, importing the interface and implementing it here is the honest
move (and is what programming to the interface is for).

Quiet by default. Lifecycle noise on every catalog build would drown the one
line a turn is actually about, so the REPL and the sidecar pass
`SilentLifecycleLog` and only the CLI's `--trace` turns reporting on.
"""
from __future__ import annotations

import sys
from typing import TextIO

from sssf_interfaces import ILifecycleLog


class SilentLifecycleLog( ILifecycleLog ):
    """Null Object: the default, so nothing has to special-case `logger is None`."""

    def created( self, obj: object ) -> None:
        return None

    def destroyed( self, obj: object ) -> None:
        return None


class StdoutLifecycleLog( ILifecycleLog ):
    """What PrintLifecycleLog was meant to be. Same two lines, valid f-strings."""

    def __init__( self, stream: TextIO | None = None ) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def created( self, obj: object ) -> None:
        print( f"[lifecycle] + { type( obj ).__name__ } #{ id( obj ):x}", file=self._stream )

    def destroyed( self, obj: object ) -> None:
        print( f"[lifecycle] - { type( obj ).__name__ } #{ id( obj ):x}", file=self._stream )

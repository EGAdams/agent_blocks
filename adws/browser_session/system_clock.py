"""IClock over the real clock."""
from __future__ import annotations

import time


class SystemClock:
    def now( self ) -> float:
        return time.monotonic()

    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )

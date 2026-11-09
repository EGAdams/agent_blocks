from __future__ import annotations

import time
from datetime import datetime


class SystemClock:
    def now(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()

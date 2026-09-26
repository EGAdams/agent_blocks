"""Stage-timing metrics for the voice pipeline.

Printed to stdout (same [stage] style as the dashboard's own
`[voice-pipeline] stage=... duration_ms=...` logging) and appended to
voice_metrics.log so a slow run can be handed back as a file instead of
retyped from a terminal scrollback.
"""
import time
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "voice_metrics.log"


def log_stage(stage: str, duration_s: float, detail: str = "") -> None:
    line = f"[metrics] ts={datetime.now().isoformat(timespec='seconds')} stage={stage} duration_ms={duration_s * 1000:.1f}"
    if detail:
        line += f" {detail}"
    print(line)
    try:
        with LOG_PATH.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


class Stopwatch:
    """`with Stopwatch("stage", detail="..."):` -- logs on exit, success or not."""

    def __init__(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.detail = detail
        self._started = 0.0

    def __enter__(self) -> "Stopwatch":
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc_info) -> None:
        log_stage(self.stage, time.perf_counter() - self._started, self.detail)

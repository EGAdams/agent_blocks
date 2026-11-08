"""Observer, with a Null Object default.

A settle loop can run for two minutes. Without an observer that is two minutes of a silent process, and
the commonest question during a relay failure -- "did it ever get there" -- has no answer. With one, the
ADW's lifecycle log carries each poll.

`NoRelayObserver` is the Null Object: the operations always have an observer, so none of them ever has to
write `if self._observer is not None`.
"""
from __future__ import annotations

from .models import SettleReport


class NoRelayObserver:
    """Null Object: told everything, says nothing. The default."""

    def waiting(self, polls: int, waited: float, note: str) -> None:
        pass

    def settled(self, report: SettleReport) -> None:
        pass


class LineObserver:
    """Writes one line per event through an injected `write` (print, a log, a list's append).

    Nothing here writes the message or the reply text -- only counts and timings. The reply is reported
    once, by the operation, in its outcome; a progress log that also carried it would put somebody's
    conversation in two places.
    """

    def __init__(self, write=print, prefix: str = "relay") -> None:
        self._write = write
        self._prefix = prefix

    def waiting(self, polls: int, waited: float, note: str) -> None:
        self._write(f"[{self._prefix}] {note}" if not polls else
                    f"[{self._prefix}] poll {polls} at {waited:g}s: {note}")

    def settled(self, report: SettleReport) -> None:
        verdict = "settled" if report.settled else "gave up"
        self._write(f"[{self._prefix}] {verdict} after {report.polls} polls, {report.waited:g}s: {report.why}")


class RecordingObserver:
    """Keeps what it was told, for tests. Same interface; no output."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.reports: list[SettleReport] = []

    def waiting(self, polls: int, waited: float, note: str) -> None:
        self.notes.append(note)

    def settled(self, report: SettleReport) -> None:
        self.reports.append(report)

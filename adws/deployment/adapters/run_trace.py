"""ITrace kept in memory for one run; the ADW writes it out with the run record."""
from __future__ import annotations

from ..interfaces import IClock
from ..models import Step


class RunTrace:
    def __init__(self, clock: IClock) -> None:
        self._clock, self._steps = clock, []

    def step(self, name: str, ok: bool = True, **detail) -> None:
        self._steps.append(Step(at=self._clock.now(), name=name, ok=ok, detail=detail))

    def steps(self) -> list[Step]:
        return list(self._steps)

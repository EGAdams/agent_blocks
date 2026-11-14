"""Strategy: what, if anything, goes wrong before a step. Test-only.

`NoFaults` is the production choice. `PlannedFaults` reads a `FaultPlan` and is
only ever built when a `--fail-at` / `--stall-at` / `--kill-at` flag was given.
"""
from __future__ import annotations

import os

from .interfaces import FaultPlan, IPhaseProbe, ISleeper


class PilotFault( RuntimeError ):
    """The intentional failure. Named so a trace reader knows it was asked for."""


class NoFaults:
    def before( self, key: str, probe: IPhaseProbe ) -> None:
        return None


class PlannedFaults:
    def __init__( self, plan: FaultPlan, sleeper: ISleeper ):
        self._plan    = plan
        self._sleeper = sleeper

    def before( self, key: str, probe: IPhaseProbe ) -> None:
        if key == self._plan.stall_at:
            # Silent on purpose: no progress event for the whole pause.
            self._sleeper.sleep( self._plan.stall_seconds )
            probe.progress( f"resumed after { self._plan.stall_seconds:g}s of silence (test fault)" )
        if key == self._plan.kill_at:
            os._exit( 137 )                       # abrupt: no finally, no cleanup, no phase_end
        if key == self._plan.fail_at:
            raise PilotFault( f"intentional failure in step { key!r} (test fault)" )

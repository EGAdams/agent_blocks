"""The pilot's work: four small steps, plain code, no SSSF.

Each one is callable and testable on its own with a fake probe. `TORTURE` is the
text the debugger has to survive: quotes, newlines, semicolons, angle brackets,
Mermaid's reserved word `end`, and a line long enough to need wrapping.
"""
from __future__ import annotations

from .interfaces import IPhaseProbe, ISleeper

TORTURE = ( 'line one; "double" and \'single\' quotes\n'
            'end\n'
            '<script>alert("x")</script> %%{init: {}}%% -->> Note over A: end;\n'
            + "long " * 60 + "tail" )


class _Step:
    name        = ""
    purpose     = ""
    description = ""

    def __init__( self, key: str, sleeper: ISleeper, pace: float ):
        self.key      = key
        self._sleeper = sleeper
        self._pace    = pace
        self.disposed = False

    def dispose( self ) -> None:
        self.disposed = True


class PrepareStep( _Step ):
    name        = "prepare"
    purpose     = "Decide which items the run will look up, and tell the reporter."
    description = "Choose the items this pilot run will process"

    def perform( self, probe: IPhaseProbe ) -> None:
        probe.progress( "choosing items" )
        self._sleeper.sleep( self._pace )
        probe.message( "report", "prepared 2 items: alpha, beta" )


class LookupStep( _Step ):
    name        = "lookup"
    purpose     = "Call the catalog tool for one item and record what it returned."
    description = "Fetch one item from the catalog tool"

    def __init__( self, key: str, sleeper: ISleeper, pace: float, item: str, answer: str ):
        super().__init__( key, sleeper, pace )
        self._item   = item
        self._answer = answer

    def perform( self, probe: IPhaseProbe ) -> None:
        call_id = probe.tool_call( "catalog.get", { "item": self._item, "fields": [ "name", "notes" ]})
        self._sleeper.sleep( self._pace )
        probe.tool_result( call_id, "catalog.get", self._answer )


class WaitStep( _Step ):
    name        = "wait_for_gate"
    purpose     = "Hold the run until the gate's deadline, then let it continue."
    description = "Pause for the release gate before reporting"

    def perform( self, probe: IPhaseProbe ) -> None:
        probe.waiting( "waiting for the release gate", deadline_seconds=self._pace * 4 )
        self._sleeper.sleep( self._pace )
        probe.progress( "gate opened" )


class ReportStep( _Step ):
    name        = "report"
    purpose     = "Summarize the run in one line."
    description = "Write the one-line summary of what the pilot did"

    def perform( self, probe: IPhaseProbe ) -> None:
        probe.progress( "writing summary" )
        self._sleeper.sleep( self._pace )
        probe.message( "prepare", "summary written: 2 items looked up" )

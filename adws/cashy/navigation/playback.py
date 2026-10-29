"""OperationPlaybackRunner: IPlaybackRunner -- Mode B, learned playback.

Runs the shared `execute_playback` operation in this process: the same
command the Smart Menu, Toyota, Sydney and the post office run, so there is
one executor and one run history. The turn's own browser is released first;
the operation attaches to the same tab (the connection remembers which).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_comms.operations.base import IOperation
from browser_workflows.interfaces.templating import IPeriodVocabulary
from browser_workflows.models import Period

from ..interfaces.learning import IWorkflowRegistry
from ..models import PlaybackResult, ResolvedWorkflow

if TYPE_CHECKING:
    from ..turn import Turn


class OperationPlaybackRunner:
    def __init__( self, execute: IOperation, registry: IWorkflowRegistry, periods: IPeriodVocabulary ) -> None:
        self._execute  = execute
        self._registry = registry
        self._periods  = periods

    def run( self, turn: "Turn", workflow: ResolvedWorkflow, period: Period | None ) -> PlaybackResult:
        turn.browser()                                  # pins the tab the operation will attach to
        turn.release()
        turn.event( "executing_adw", workflow.playback )
        args: dict[ str, object ] = { "name": workflow.playback, "actor": turn.actor, "port": turn.services.port }
        if period:
            v = self._periods.values( period )
            args[ "period" ] = f"{ v[ 'month_name' ]} { v[ 'year' ]}"
        outcome = self._execute.run( args )
        run_id  = int( outcome.data.get( "run_id" ) or 0 )
        version = workflow.version
        if outcome.ok and version and version.status == "candidate":
            self._registry.set_status( version.id, "verified", run_id )
        return PlaybackResult( ok=outcome.ok, run_id=run_id, state=str( outcome.data.get( "state" ) or "" ),
                               message=outcome.line )

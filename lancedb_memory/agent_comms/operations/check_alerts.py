"""CheckAlerts -- the failures waiting for one agent, optionally acknowledging them.

Sweeps for tasks whose worker died first, so a killed worker shows up here
as a failure (or quietly goes back in the queue) instead of never at all.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..alerts import StalledTaskSweeper
from ..ports import IAlertBoard
from ._named import Named
from .base import OperationOutcome


class CheckAlerts( Named ):
    NAME        = "check_alerts"
    DESCRIPTION = "List the failed tasks one agent has not yet been told about, and optionally acknowledge them"

    def __init__( self, board: IAlertBoard, sweeper: Callable[[], StalledTaskSweeper ]) -> None:
        self._board   = board
        self._sweeper = sweeper

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        agent = str( args[ "agent" ])
        self._sweeper().sweep()
        alerts = self._board.pending( agent )
        data   = { "alerts": [ a.model_dump( mode="json" ) for a in alerts ]}
        if args.get( "acknowledge" ):
            data[ "acknowledged" ] = self._board.acknowledge( agent, [ a.id for a in alerts ])
        if not alerts:
            return OperationOutcome( ok=True, line=f"No failed tasks waiting for { agent }.", data=data )
        return OperationOutcome( ok=True, line=" ".join( a.spoken() for a in alerts ), data=data )

"""CancelTask -- withdraw a task nobody has started."""
from __future__ import annotations

from typing import Any

from ..board import TaskCanceller
from ._named import Named
from .base import OperationOutcome


class CancelTask( Named ):
    NAME        = "cancel_task"
    DESCRIPTION = "Withdraw a queued task before any worker claims it"

    def __init__( self, canceller: TaskCanceller ) -> None:
        self._canceller = canceller

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        line = self._canceller.cancel( str( args[ "task_id" ]), by=str( args.get( "sender", "someone" )))
        return OperationOutcome( ok=line.startswith( "Cancelled" ), line=line )

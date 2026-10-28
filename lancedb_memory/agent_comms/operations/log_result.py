"""LogResult -- put one observation on the event log against a task."""
from __future__ import annotations

from typing import Any

from ..models import TaskEvent
from ..ports import IEventPublisher
from ._named import Named
from .base import OperationOutcome


class LogResult( Named ):
    NAME        = "log_result"
    DESCRIPTION = "Record an execution result or note against a task on the event log"

    def __init__( self, events: IEventPublisher ) -> None:
        self._events = events

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        task_id = str( args.get( "task_id" ) or "-" )
        self._events.publish( TaskEvent( name=str( args.get( "event", "task.note" )), task_id=task_id,
                                         agent=str( args[ "agent" ]),
                                         details={ "note": str( args.get( "note", "" ))}))
        return OperationOutcome( ok=True, line=f"Logged against { task_id }." )

"""TaskStatus -- where one task is, or everything one sender has asked for."""
from __future__ import annotations

from typing import Any

from ..models import Envelope, TaskStatus
from ..ports import ITaskBoard
from ._named import Named
from .base import OperationOutcome


_SPOKEN = {
    TaskStatus.QUEUED:    "is waiting its turn",
    TaskStatus.RUNNING:   "is in progress",
    TaskStatus.SUCCEEDED: "succeeded",
    TaskStatus.FAILED:    "failed",
    TaskStatus.CANCELLED: "was cancelled",
    TaskStatus.TIMED_OUT: "timed out",
}


def describe( task: Envelope ) -> str:
    said = f": { task.result or task.error }" if ( task.result or task.error ) else ""
    return f"{ task.task_type } for { task.recipient } { _SPOKEN[ task.status ]}{ said[ :300 ]}"


class TaskStatusReport( Named ):
    NAME        = "task_status"
    DESCRIPTION = "Report where a task is, or every task a sender has open"

    def __init__( self, board: ITaskBoard ) -> None:
        self._board = board

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        if args.get( "task_id" ):
            task = self._board.task( str( args[ "task_id" ]))
            if task is None:
                return OperationOutcome( ok=False, line=f"There's no task { args[ 'task_id' ]}." )
            return OperationOutcome( ok=True, line=_end( describe( task )),
                                     data={ "task": task.model_dump( mode="json" )})
        # Newest first: the one just asked about is the one wanted.
        tasks = sorted( self._board.tasks_from( str( args[ "sender" ])), key=lambda t: t.created_at,
                        reverse=True )[ :int( args.get( "limit", 5 ))]
        if not tasks:
            return OperationOutcome( ok=True, line="You haven't sent any tasks yet.", data={ "tasks": []})
        return OperationOutcome( ok=True, line=_end( "; ".join( describe( t ) for t in tasks )),
                                 data={ "tasks": [ t.model_dump( mode="json" ) for t in tasks ]})


def _end( text: str ) -> str:
    return text if text.endswith(( ".", "!", "?" )) else text + "."

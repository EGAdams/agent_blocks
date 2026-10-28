"""RetryTask -- send a failed task again, as a new task linked to the old one.

A failed task is terminal (state.py), so a retry is a new request with the
old one as its parent. Work that is not idempotent -- a build may have
changed files before it failed -- is only re-sent when the caller says so
explicitly with `confirm`, never by default.
"""
from __future__ import annotations

from typing import Any

from ..models import TaskRequest, TaskStatus
from ..ports import ITaskBoard, ITaskDispatcher
from ._named import Named
from .base import OperationOutcome


class RetryTask( Named ):
    NAME        = "retry_task"
    DESCRIPTION = "Send a failed or cancelled task again as a new linked task"

    def __init__( self, board: ITaskBoard, dispatcher: ITaskDispatcher ) -> None:
        self._board      = board
        self._dispatcher = dispatcher

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        old = self._board.task( str( args[ "task_id" ]))
        if old is None:
            return OperationOutcome( ok=False, line=f"There's no task { args[ 'task_id' ]}." )
        if old.status not in ( TaskStatus.FAILED, TaskStatus.CANCELLED ):
            return OperationOutcome( ok=False, line=f"Task { old.id } is { old.status.value }; only a failed "
                                                    "or cancelled task is retried." )
        if not old.idempotent and not args.get( "confirm" ):
            return OperationOutcome( ok=False, line=f"Task { old.id } may already have changed files, so it "
                                                    "is only sent again when you confirm it." )
        receipt = self._dispatcher.send( TaskRequest( sender=old.sender, recipient=old.recipient,
                                                      task_type=old.task_type, text=old.text, context=old.context,
                                                      priority=old.priority, parent_id=old.id, wait_seconds=0 ))
        if not receipt.accepted:
            return OperationOutcome( ok=False, line=receipt.reason )
        return OperationOutcome( ok=True, line=f"Sent again as task { receipt.task_id }.",
                                 data={ "task_id": receipt.task_id, "retry_of": old.id })

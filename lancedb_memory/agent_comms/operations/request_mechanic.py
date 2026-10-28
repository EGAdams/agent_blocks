"""RequestMechanic -- ask the mechanic to diagnose one task, and wait for the answer."""
from __future__ import annotations

from typing import Any

from ..models import TaskRequest
from ..ports import ITaskDispatcher
from ._named import Named
from .base import OperationOutcome


class RequestMechanic( Named ):
    NAME        = "request_mechanic"
    DESCRIPTION = "Ask the mechanic to diagnose a failed task and report back"

    def __init__( self, dispatcher: ITaskDispatcher, mechanic: str = "mechanic" ) -> None:
        self._dispatcher = dispatcher
        self._mechanic   = mechanic

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        task_id = str( args[ "task_id" ])
        receipt = self._dispatcher.send( TaskRequest(
            sender=str( args[ "sender" ]), recipient=self._mechanic, task_type="diagnose",
            text=str( args.get( "text" ) or f"Diagnose task { task_id }" ),
            context={ "task_id": task_id, **({ "adw_id": args[ "adw_id" ]} if args.get( "adw_id" ) else {})},
            wait_seconds=float( args.get( "wait_seconds", 60 ))))
        if not receipt.accepted:
            return OperationOutcome( ok=False, line=receipt.reason )
        if receipt.reply is None:
            return OperationOutcome( ok=True, line=f"The mechanic has it as task { receipt.task_id }.",
                                     data={ "task_id": receipt.task_id, "pending": True })
        return OperationOutcome( ok=receipt.reply.error is None, line=receipt.reply.text,
                                 data={ "task_id": receipt.task_id, "pending": False })

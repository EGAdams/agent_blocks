"""SendMessage -- post a request through the dispatcher, without waiting."""
from __future__ import annotations

from typing import Any

from ..models import TaskRequest
from ..ports import IAgentRegistry, ITaskDispatcher
from ._named import Named
from .base import OperationOutcome


class SendMessage( Named ):
    NAME        = "send_message"
    DESCRIPTION = "Post a task request to its recipient's mailbox and wake a worker"

    def __init__( self, dispatcher: ITaskDispatcher, registry: IAgentRegistry ) -> None:
        self._dispatcher = dispatcher
        self._registry   = registry

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        request = TaskRequest.model_validate( args[ "request" ])
        card    = self._registry.card( request.recipient )
        cap     = card.capability( request.task_type ) if card else None
        # How long the caller means to wait is the next step's business; the
        # send itself never blocks.
        wait    = request.wait_seconds if request.wait_seconds is not None else \
                  ( cap.default_wait_seconds if cap else 0.0 )
        receipt = self._dispatcher.send( request.model_copy( update={ "wait_seconds": 0 }))
        if not receipt.accepted:
            return OperationOutcome( ok=False, line=receipt.reason, data={ "refused": True })
        who = card.display_name if card else request.recipient
        return OperationOutcome( ok=True, line=f"Sent to { who } as task { receipt.task_id }.",
                                 data={ "task_id": receipt.task_id, "sender": request.sender,
                                        "recipient": request.recipient, "wait_seconds": wait,
                                        "asynchronous": wait <= 0 })

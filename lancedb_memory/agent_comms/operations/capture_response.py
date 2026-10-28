"""CaptureResponse -- wait for, and take, the reply to one task."""
from __future__ import annotations

from typing import Any

from ..models import TaskStatus
from ..ports import IRequestReply
from ._named import Named
from .base import OperationOutcome


class CaptureResponse( Named ):
    NAME        = "capture_response"
    DESCRIPTION = "Wait for the reply to a task and take it out of the sender's inbox"

    def __init__( self, replies: IRequestReply ) -> None:
        self._replies = replies

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        task_id = str( args[ "task_id" ])
        wait    = float( args.get( "wait_seconds" ) or 0 )
        reply   = self._replies.await_reply( str( args[ "sender" ]), task_id, wait )
        if reply is None:
            return OperationOutcome( ok=True, data={ "pending": True },
                                     line=f"No answer yet on task { task_id }; it is still running and "
                                          "the reply will be kept for you." )
        ok = reply.outcome is TaskStatus.SUCCEEDED
        return OperationOutcome( ok=ok, line=reply.text,
                                 data={ "reply": reply.model_dump( mode="json" ), "pending": False })

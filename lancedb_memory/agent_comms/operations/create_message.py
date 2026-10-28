"""CreateMessage -- build a TaskRequest from explicit fields, validated by its model."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..models import TaskRequest
from ._named import Named
from .base import OperationOutcome

_FIELDS = ( "sender", "recipient", "task_type", "text", "context", "priority", "wait_seconds", "parent_id" )


class CreateMessage( Named ):
    NAME        = "create_message"
    DESCRIPTION = "Create a structured task request from explicit fields"

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        try:
            request = TaskRequest( **{ k: args[ k ] for k in _FIELDS if args.get( k ) is not None })
        except ValidationError as exc:
            return OperationOutcome( ok=False, line=f"That request is malformed: { exc.errors()[ 0 ][ 'msg' ]}." )
        return OperationOutcome( ok=True, line=f"Request for { request.recipient } created.",
                                 data={ "request": request.model_dump()})

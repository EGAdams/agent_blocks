"""ValidateTask -- would the post office accept this request? Sends nothing."""
from __future__ import annotations

from typing import Any

from ..models import TaskRequest
from ..ports import IRequestValidator
from ._named import Named
from .base import OperationOutcome


class ValidateTask( Named ):
    NAME        = "validate_task"
    DESCRIPTION = "Check a request against the registry: known agents, real capability, no loop"

    def __init__( self, validator: IRequestValidator ) -> None:
        self._validator = validator

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        request = TaskRequest.model_validate( args[ "request" ])
        problem = self._validator.problem( request )
        if problem:
            return OperationOutcome( ok=False, line=problem, data={ "refused": True })
        return OperationOutcome( ok=True, line="The request is valid." )

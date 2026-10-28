"""RouteRequest -- free text in, an addressed TaskRequest out. The router is injected."""
from __future__ import annotations

from typing import Any

from ..ports import IMessageRouter
from ._named import Named
from .base import OperationOutcome


class RouteRequest( Named ):
    NAME        = "route_request"
    DESCRIPTION = "Read who a request is for and what kind of work it is"

    def __init__( self, router: IMessageRouter ) -> None:
        self._router = router

    @property
    def performer( self ) -> str:
        return type( self._router ).__name__

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        request = self._router.route( str( args[ "sender" ]), str( args[ "text" ]))
        if request is None:
            return OperationOutcome( ok=False, line="I couldn't tell which agent that is for. Name one." )
        if args.get( "context" ):
            request = request.model_copy( update={ "context": { **request.context, **args[ "context" ]}})
        for key in ( "priority", "wait_seconds" ):
            if args.get( key ) is not None:
                request = request.model_copy( update={ key: args[ key ]})
        return OperationOutcome( ok=True, line=f"That's { request.task_type } work for { request.recipient }.",
                                 data={ "request": request.model_dump()})

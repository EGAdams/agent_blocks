"""What the operations share: the outcome type, a base with the IOperation
properties, and the argument helpers. No behaviour of its own."""
from __future__ import annotations

from typing import Any

from agent_comms.operations.base import OperationOutcome
from browser_session.models import DebugEndpoint

from ..errors import BrowserWorkflowError
from ..models import LocatorCandidate

__all__ = [ "Operation", "OperationOutcome", "ok", "refused", "endpoint", "locators_from", "positions" ]


class Operation:
    """Name, description and performer as class attributes; `run` is the subclass's."""
    name        = ""
    description = ""
    performer   = "browser_workflows"

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        try:
            return self.perform( args )
        except BrowserWorkflowError as exc:
            return refused( str( exc ))

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        raise NotImplementedError


def ok( line: str, **data: Any ) -> OperationOutcome:
    return OperationOutcome( ok=True, line=line, data=data )


def refused( line: str, **data: Any ) -> OperationOutcome:
    return OperationOutcome( ok=False, line=line, data=data )


def endpoint( args: dict[ str, Any ]) -> DebugEndpoint:
    return DebugEndpoint( port=int( args.get( "port" ) or 9222 ))


def locators_from( args: dict[ str, Any ]) -> list[ LocatorCandidate ]:
    """--css / --text / --label, best first; --locators takes a JSON list."""
    found = [ LocatorCandidate.model_validate( c ) for c in args.get( "locators" ) or []]
    for strategy, rank in (( "css", 20 ), ( "label", 30 ), ( "text", 40 )):
        if args.get( strategy ):
            found.append( LocatorCandidate( strategy=strategy, value=str( args[ strategy ]), source="given", rank=rank ))
    if not found:
        raise BrowserWorkflowError( "say which element: --css, --label or --text" )
    return found


def positions( text: str | None ) -> set[ int ]:
    try:
        return { int( p ) for p in ( text or "" ).replace( " ", "" ).split( "," ) if p }
    except ValueError:
        raise BrowserWorkflowError( f"--approve takes step numbers like 3,7, not { text!r}" )

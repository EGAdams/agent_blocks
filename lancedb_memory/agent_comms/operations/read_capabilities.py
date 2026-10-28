"""ReadCapabilities -- what one agent can actually do, from its card."""
from __future__ import annotations

from typing import Any

from ..ports import IAgentRegistry
from ._named import Named
from .base import OperationOutcome


class ReadCapabilities( Named ):
    NAME        = "read_capabilities"
    DESCRIPTION = "Read one agent's capabilities from its registry card"

    def __init__( self, registry: IAgentRegistry ) -> None:
        self._registry = registry

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        agent = str( args.get( "agent", "" ))
        card  = self._registry.card( agent )
        if card is None:
            return OperationOutcome( ok=False, line=f"There's no agent called { agent }." )
        caps = [ c.model_dump() for c in card.capabilities ]
        line = f"{ card.display_name } can do: " + "; ".join(
            f"{ c.task_type } ({ c.description })" for c in card.capabilities ) + "."
        return OperationOutcome( ok=True, line=line, data={ "capabilities": caps, "runtime": card.runtime })

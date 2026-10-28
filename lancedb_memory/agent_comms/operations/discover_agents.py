"""DiscoverAgents -- who is registered with the post office."""
from __future__ import annotations

from typing import Any

from ..ports import IAgentRegistry
from ._named import Named
from .base import OperationOutcome


class DiscoverAgents( Named ):
    NAME        = "discover_agents"
    DESCRIPTION = "List the agents registered with the post office"

    def __init__( self, registry: IAgentRegistry ) -> None:
        self._registry = registry

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        names = [ n for n in self._registry.names() if self._registry.card( n ).capabilities ]
        return OperationOutcome( ok=True, line="Registered agents: " + ", ".join( names ) + ".",
                                 data={ "agents": names })

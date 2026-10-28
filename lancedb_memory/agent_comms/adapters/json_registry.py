"""JsonAgentRegistry -- the registered agents, read from one JSON file.

Adding an agent is one entry in `agents.json` (plus, if its runtime is new,
one executor factory). No participant needs to change: senders discover the
newcomer through IAgentRegistry, the router learns its name from here, and
validation reads its capabilities from its card.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import AgentCard

class JsonAgentRegistry:
    """IAgentRegistry. Loaded once; a bad card fails loud at load time."""

    def __init__( self, path: Path ) -> None:
        raw = json.loads( Path( path ).read_text( encoding="utf-8" ))
        cards = [ AgentCard.model_validate( entry ) for entry in raw[ "agents" ]]
        self._cards = { card.name: card for card in cards }
        if len( self._cards ) != len( cards ):
            raise ValueError( f"{ path }: two agents share a name" )
        # Spoken repository names -> paths, longest first so "letta-code
        # dashboard" wins over "letta-code".
        repos = { str( k ): str( v ) for k, v in raw.get( "repositories", {}).items()}
        self.repositories = dict( sorted( repos.items(), key=lambda kv: -len( kv[ 0 ])))

    def names( self ) -> list[ str ]:
        return list( self._cards )

    def card( self, name: str ) -> AgentCard | None:
        return self._cards.get( name )

class InMemoryAgentRegistry:
    """IAgentRegistry over cards built in code. For tests and composition."""

    def __init__( self, cards: list[ AgentCard ]) -> None:
        self._cards = { card.name: card for card in cards }

    def names( self ) -> list[ str ]:
        return list( self._cards )

    def card( self, name: str ) -> AgentCard | None:
        return self._cards.get( name )

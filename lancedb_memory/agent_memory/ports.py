"""The Protocols. High-level policy depends on these and on nothing else."""
from datetime import datetime
from typing import Protocol

from .models import AgentReply, EmbeddedTurn, MemoryQuery, RecalledTurn, Turn


class Embedder( Protocol ):
    @property
    def dimensions( self ) -> int: ...

    async def embed( self, text: str ) -> list[ float ]: ...


class MemoryStore( Protocol ):
    """Repository. Owns persistence and retrieval; the agent owns neither."""

    def add( self, turns: list[ EmbeddedTurn ]) -> None: ...

    def search( self, query: MemoryQuery ) -> list[ RecalledTurn ]: ...

    def recent( self, session_id: str, limit: int ) -> list[ Turn ]:
        """The last `limit` turns by time, no vector involved. Newest first."""
        ...

    def forget( self, ids: list[ str ]) -> int:
        """Remove these turns. Returns how many were there to remove.

        Recall treats a stored turn as history, so a turn the agent got wrong
        keeps being handed back to it as established fact. Deleting is the
        only way out of that loop, which is why it belongs on the port rather
        than in a one-off script.
        """
        ...


class RecallStrategy( Protocol ):
    """Decides WHICH turns become context. Swappable policy."""

    def recall( self, session_id: str, query_vector: list[ float ]) -> list[ Turn ]: ...


class PromptComposer( Protocol ):
    def compose( self, history: list[ Turn ], user_message: str ) -> str: ...


class LanguageModel( Protocol ):
    async def complete( self, prompt: str ) -> AgentReply: ...


class TurnObserver( Protocol ):
    """Side effects that are NOT the agent's job: logging, cost, JSON archive."""

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None: ...


class Clock( Protocol ):
    def now( self ) -> datetime: ...

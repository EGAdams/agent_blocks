"""Pydantic data contracts. Everything crossing a port boundary is one of these."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

Speaker = Literal[ "USER", "AGENT" ]


class Turn( BaseModel ):
    """One utterance. The unit of storage and of recall."""
    id: UUID = Field( default_factory=uuid4 )
    session_id: str
    speaker: Speaker
    message: str
    created_at: datetime


class EmbeddedTurn( BaseModel ):
    """A Turn plus its vector -- what crosses into the storage layer."""
    turn: Turn
    vector: list[ float ]


class RecalledTurn( BaseModel ):
    """A Turn plus why it came back. Distance is store-supplied, not agent-computed."""
    turn: Turn
    distance: float


class MemoryQuery( BaseModel ):
    """Explicit query object so the store signature never grows more parameters."""
    vector: list[ float ]
    session_id: str | None = None   # None means search across every session
    limit: int = 30


class AgentReply( BaseModel ):
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

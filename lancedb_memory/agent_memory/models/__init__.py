"""Pydantic data contracts. Everything crossing a port boundary is one of these.

One class per file (see the sibling modules); re-exported here so every
existing `from .models import X` / `from ..models import X` import elsewhere
in agent_memory keeps working unchanged.
"""
from .agent_reply import AgentReply
from .compact_result import CompactResult
from .embedded_turn import EmbeddedTurn
from .memory_query import MemoryQuery
from .recalled_turn import RecalledTurn
from .turn import Speaker, Turn

__all__ = [
    "AgentReply",
    "CompactResult",
    "EmbeddedTurn",
    "MemoryQuery",
    "RecalledTurn",
    "Speaker",
    "Turn",
]

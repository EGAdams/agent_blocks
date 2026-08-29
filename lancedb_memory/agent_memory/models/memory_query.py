"""Pydantic data contract. Everything crossing a port boundary is one of these."""
from pydantic import BaseModel


class MemoryQuery( BaseModel ):
    """Explicit query object so the store signature never grows more parameters."""
    vector: list[ float ]
    session_id: str | None = None   # None means search across every session
    limit: int = 30

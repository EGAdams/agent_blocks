"""Pydantic data contract. Everything crossing a port boundary is one of these."""
from pydantic import BaseModel

from .turn import Turn


class EmbeddedTurn( BaseModel ):
    """A Turn plus its vector -- what crosses into the storage layer."""
    turn: Turn
    vector: list[ float ]

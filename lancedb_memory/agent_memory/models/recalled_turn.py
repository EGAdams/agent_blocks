"""Pydantic data contract. Everything crossing a port boundary is one of these."""
from pydantic import BaseModel

from .turn import Turn


class RecalledTurn( BaseModel ):
    """A Turn plus why it came back. Distance is store-supplied, not agent-computed."""
    turn: Turn
    distance: float

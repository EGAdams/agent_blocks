"""Pydantic data contract. Everything crossing a port boundary is one of these."""
from pydantic import BaseModel


class CompactResult( BaseModel ):
    """What a compact did: the new session, the summary it starts from, and
    how many turns that summary replaced in recall."""
    session_id: str
    summary: str
    turns_before: int

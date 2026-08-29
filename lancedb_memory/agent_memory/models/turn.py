"""Pydantic data contract. Everything crossing a port boundary is one of these."""
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

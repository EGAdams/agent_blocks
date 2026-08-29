"""Pydantic data contract. Everything crossing a port boundary is one of these."""
from pydantic import BaseModel


class AgentReply( BaseModel ):
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

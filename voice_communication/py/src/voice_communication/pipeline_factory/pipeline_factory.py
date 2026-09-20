"""Factory Method port for constructing Pipecat pipelines."""
from typing import Protocol
from uuid import UUID

from ..pydantic_models.models import PipelineHandle


class PipelineFactory(Protocol):
    """Creates pipelines without leaking concrete Pipecat providers."""

    def create(self, session_id: UUID) -> PipelineHandle:
        ...

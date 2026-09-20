"""Speech-to-text Strategy port for the separate Pipecat service."""
from typing import Protocol

from ..pydantic_models.models import AudioFrame, TranscriptEvent


class TranscriptionStrategy(Protocol):
    async def transcribe(
        self,
        frame: AudioFrame,
        generation: int,
    ) -> TranscriptEvent:
        ...

"""Text-to-speech Strategy port for the separate Pipecat service."""
from collections.abc import AsyncIterator
from typing import Protocol

from ..pydantic_models.models import AudioFrame, SpeakRequest


class SpeechSynthesizer(Protocol):
    def synthesize(self, request: SpeakRequest) -> AsyncIterator[AudioFrame]:
        ...

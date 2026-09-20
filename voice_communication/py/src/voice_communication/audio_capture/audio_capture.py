"""Audio-input port owned by the separate Pipecat service."""
from collections.abc import AsyncIterator
from typing import Protocol

from ..pydantic_models.models import AudioFrame


class AudioCapture(Protocol):
    """Provides validated microphone frames without exposing the transport."""

    def frames(self) -> AsyncIterator[AudioFrame]:
        ...

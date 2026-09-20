"""Turn-detection Strategy port for the separate Pipecat service."""
from typing import Protocol

from ..pydantic_models.models import AudioFrame


class DetectionInterface(Protocol):
    def is_speech(self, frame: AudioFrame) -> bool:
        ...

    def is_turn_complete(self) -> bool:
        ...

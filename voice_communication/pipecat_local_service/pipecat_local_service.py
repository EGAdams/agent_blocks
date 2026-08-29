"""Facade port for Pipecat running as a separate local service."""
from typing import Protocol

from ..pydantic_models.models import (
    AudioFrame,
    StartSessionRequest,
    StartSessionResponse,
    StopSessionRequest,
    VoiceHealthEvent,
)


class PipecatLocalService(Protocol):
    """The dashboard depends on this boundary, never on Pipecat directly."""

    async def start_session(
        self,
        request: StartSessionRequest,
    ) -> StartSessionResponse:
        ...

    async def submit_audio(self, frame: AudioFrame) -> None:
        ...

    async def stop_session(self, request: StopSessionRequest) -> None:
        ...

    async def health(self) -> VoiceHealthEvent:
        ...

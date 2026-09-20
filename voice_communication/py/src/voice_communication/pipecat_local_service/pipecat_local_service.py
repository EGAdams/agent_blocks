"""Facade port for Pipecat running as a separate local service."""
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from ..pipeline_factory.pipeline_factory import PipelineFactory
from ..pydantic_models.models import (
    AudioFrame,
    PipelineHandle,
    StartSessionRequest,
    StartSessionResponse,
    StopSessionRequest,
    VoiceHealthEvent,
    VoiceHealthStatus,
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


class PipecatLocalServiceFacade:
    """
    Concrete Facade over one PipelineFactory: owns the session_id -> pipeline
    map, and nothing else. Turn detection, transcription and synthesis are
    PipelineFactory's dependencies to wire, not this class's.
    """

    def __init__(self, pipeline_factory: PipelineFactory) -> None:
        self._pipeline_factory = pipeline_factory
        self._sessions: dict[UUID, PipelineHandle] = {}

    async def start_session(
        self,
        request: StartSessionRequest,
    ) -> StartSessionResponse:
        session_id = uuid4()
        self._sessions[session_id] = self._pipeline_factory.create(session_id)
        return StartSessionResponse(session_id=session_id)

    async def submit_audio(self, frame: AudioFrame) -> None:
        if frame.session_id not in self._sessions:
            raise KeyError(f"submit_audio: unknown session {frame.session_id}")

    async def stop_session(self, request: StopSessionRequest) -> None:
        self._sessions.pop(request.session_id, None)

    async def health(self) -> VoiceHealthEvent:
        start = perf_counter()
        session_count = len(self._sessions)
        latency_ms = (perf_counter() - start) * 1000
        return VoiceHealthEvent(
            component="pipecat_local_service",
            status=VoiceHealthStatus.HEALTHY,
            detail=f"{session_count} session(s) live",
            latency_ms=latency_ms,
        )

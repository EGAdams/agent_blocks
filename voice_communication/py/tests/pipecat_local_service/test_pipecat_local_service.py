"""PipecatLocalServiceFacade: session lifecycle and health() round-tripping."""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from voice_communication.pipecat_local_service.pipecat_local_service import (
    PipecatLocalServiceFacade,
)
from voice_communication.pydantic_models.models import (
    AudioFrame,
    PipelineHandle,
    StartSessionRequest,
    StopSessionRequest,
    VoiceHealthStatus,
)


class FakePipelineFactory:
    """Hands out a pipeline handle without ever touching real Pipecat."""

    def create(self, session_id: UUID) -> PipelineHandle:
        return PipelineHandle(session_id=session_id, pipeline_id=uuid4())


def test_health_round_trips_through_camel_case_serialization() -> None:
    service = PipecatLocalServiceFacade(FakePipelineFactory())

    event = asyncio.run(service.health())
    payload = event.model_dump(by_alias=True)

    assert event.status == VoiceHealthStatus.HEALTHY
    assert payload["latencyMs"] == event.latency_ms
    assert "latency_ms" not in payload


def test_start_session_hands_out_a_fresh_session_id() -> None:
    service = PipecatLocalServiceFacade(FakePipelineFactory())

    response = asyncio.run(service.start_session(StartSessionRequest(agent_id="agent-1")))

    assert isinstance(response.session_id, UUID)


def test_submit_audio_for_an_unknown_session_raises() -> None:
    service = PipecatLocalServiceFacade(FakePipelineFactory())
    frame = AudioFrame(session_id=uuid4(), sequence=0, pcm_base64="AA==")

    try:
        asyncio.run(service.submit_audio(frame))
    except KeyError:
        return
    raise AssertionError("expected KeyError for an unknown session")


def test_submit_audio_for_a_started_session_does_not_raise() -> None:
    service = PipecatLocalServiceFacade(FakePipelineFactory())
    response = asyncio.run(service.start_session(StartSessionRequest(agent_id="agent-1")))
    frame = AudioFrame(session_id=response.session_id, sequence=0, pcm_base64="AA==")

    asyncio.run(service.submit_audio(frame))


def test_stop_session_is_idempotent_for_an_unknown_session() -> None:
    service = PipecatLocalServiceFacade(FakePipelineFactory())

    asyncio.run(service.stop_session(StopSessionRequest(session_id=uuid4())))

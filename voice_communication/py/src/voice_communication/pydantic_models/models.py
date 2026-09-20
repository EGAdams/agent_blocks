"""Validated request and event shapes for the local Pipecat service."""
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class VoiceModel(BaseModel):
    """Fail-closed base for every cross-process voice shape."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class AgentEventKind(str, Enum):
    REASONING = "reasoning"
    ASSISTANT_TEXT = "assistant_text"
    TOOL = "tool"
    TERMINAL = "terminal"
    ERROR = "error"


class VoiceHealthStatus(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class StartSessionRequest(VoiceModel):
    agent_id: str = Field(min_length=1)
    conversation_id: str | None = None


class StartSessionResponse(VoiceModel):
    session_id: UUID


class StopSessionRequest(VoiceModel):
    session_id: UUID


class AudioFrame(VoiceModel):
    session_id: UUID
    sequence: int = Field(ge=0)
    pcm_base64: str = Field(min_length=1)


class TranscriptEvent(VoiceModel):
    session_id: UUID
    generation: int = Field(ge=0)
    text: str
    final: bool


class AgentEvent(VoiceModel):
    session_id: UUID
    generation: int = Field(ge=0)
    kind: AgentEventKind
    text: str


class SpeakRequest(VoiceModel):
    session_id: UUID
    generation: int = Field(ge=0)
    text: str = Field(min_length=1)
    voice: str | None = None


class VoiceHealthEvent(VoiceModel):
    component: str = Field(min_length=1)
    status: VoiceHealthStatus
    detail: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)


class PipelineHandle(VoiceModel):
    session_id: UUID
    pipeline_id: UUID

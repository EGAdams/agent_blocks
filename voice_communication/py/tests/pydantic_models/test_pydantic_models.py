"""The shared cross-process wire fixtures: Python's half of the step-7 check."""
from __future__ import annotations

import json
from pathlib import Path

from voice_communication.pydantic_models.models import VoiceHealthEvent, VoiceHealthStatus

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_FIXTURE = _REPO_ROOT / "contracts" / "fixtures" / "voice_health_event.json"


def test_python_accepts_the_same_wire_fixture_ts_does() -> None:
    raw = _FIXTURE.read_text(encoding="utf-8")

    event = VoiceHealthEvent.model_validate_json(raw)

    assert event.component == "pipecat_local_service"
    assert event.status == VoiceHealthStatus.HEALTHY
    assert event.latency_ms == 12.5
    assert json.loads(raw) == event.model_dump(mode="json", by_alias=True)

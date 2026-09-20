"""Fails when the TypeScript and pydantic catalogs drift out of shape parity.

This is the only test that spans both package roots, and the reason they share
one repository: the two catalogs describe one wire format twice, so a change to
either side is a defect until the other side changes in the same commit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py" / "src"))

from voice_communication.pydantic_models import models as py_models  # noqa: E402

_CONTRACTS_TS = _ROOT / "ts" / "src" / "typescript_contracts" / "contracts.ts"

# Field types are allowed to differ where the wire representation and the
# validated runtime type legitimately diverge; field *names* never may.
DOCUMENTED_TYPE_DIVERGENCES = {
    ("StartSessionResponse", "sessionId"),
    ("StopSessionRequest", "sessionId"),
    ("AudioFrame", "sessionId"),
    ("TranscriptEvent", "sessionId"),
    ("AgentEvent", "sessionId"),
    ("SpeakRequest", "sessionId"),
    ("PipelineHandle", "sessionId"),
    ("PipelineHandle", "pipelineId"),
}


def _ts_source() -> str:
    return _CONTRACTS_TS.read_text(encoding="utf-8")


def ts_interfaces() -> dict[str, set[str]]:
    """Interface name -> its field names, as written on the browser side."""
    source = _ts_source()
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", source, re.S):
        name, body = match.group(1), match.group(2)
        found[name] = set(re.findall(r"readonly (\w+)\??:", body))
    return found


def ts_string_unions() -> dict[str, set[str]]:
    """String-union alias name -> its literal values."""
    source = _ts_source()
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"export type (\w+) =((?:[^;]|\n)*?);", source):
        name, body = match.group(1), match.group(2)
        values = set(re.findall(r'"([^"]+)"', body))
        if values:
            found[name] = values
    return found


def py_shapes() -> dict[str, set[str]]:
    """VoiceModel subclass name -> its serialized (camelCase) field names."""
    found: dict[str, set[str]] = {}
    for name in dir(py_models):
        obj = getattr(py_models, name)
        if not isinstance(obj, type) or not issubclass(obj, py_models.VoiceModel):
            continue
        if obj is py_models.VoiceModel:
            continue
        found[name] = {
            field.serialization_alias or field.alias or field_name
            for field_name, field in obj.model_fields.items()
        }
    return found


def py_enums() -> dict[str, set[str]]:
    """str-Enum name -> its member values."""
    import enum

    found: dict[str, set[str]] = {}
    for name in dir(py_models):
        obj = getattr(py_models, name)
        if isinstance(obj, type) and issubclass(obj, enum.Enum) and issubclass(obj, str):
            found[name] = {member.value for member in obj}
    return found


def test_same_shape_names() -> None:
    assert ts_interfaces().keys() == py_shapes().keys(), (
        "shape names drifted between the two catalogs"
    )


def test_same_field_names() -> None:
    ts, py = ts_interfaces(), py_shapes()
    for name in sorted(ts.keys() & py.keys()):
        assert ts[name] == py[name], f"{name} field names drifted: {ts[name] ^ py[name]}"


def test_same_enum_names_and_values() -> None:
    ts, py = ts_string_unions(), py_enums()
    assert ts.keys() == py.keys(), "enumerated-kind names drifted between the catalogs"
    for name in sorted(ts.keys() & py.keys()):
        assert ts[name] == py[name], f"{name} values drifted: {ts[name] ^ py[name]}"


def test_documented_divergences_still_exist() -> None:
    """Keeps the exception list honest: a stale entry is itself drift."""
    ts = ts_interfaces()
    for shape, field in sorted(DOCUMENTED_TYPE_DIVERGENCES):
        assert shape in ts, f"documented divergence names a dropped shape: {shape}"
        assert field in ts[shape], f"documented divergence names a dropped field: {shape}.{field}"


def main() -> int:
    failures: list[str] = []
    for check in (
        test_same_shape_names,
        test_same_field_names,
        test_same_enum_names_and_values,
        test_documented_divergences_still_exist,
    ):
        try:
            check()
        except AssertionError as error:
            failures.append(f"{check.__name__}: {error}")
    for failure in failures:
        print(f"FAIL {failure}")
    if not failures:
        print(f"contract parity OK ({len(ts_interfaces())} shapes, {len(ts_string_unions())} enums)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

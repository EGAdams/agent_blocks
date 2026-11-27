"""Shared helpers: bounded, credential-free text and the identifiers found in it."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from agent_bridge_worker.redaction import SecretScanner
from agent_comms.live_output import clean

_SCANNER = SecretScanner()
REQUEST_ID = re.compile(r"\brq_[0-9a-f]{12,}\b")
BRIDGE_TASK = re.compile(r"\bt_[0-9a-f]{12,}\b")


def safe(text: object, clip: int = 600) -> str:
    out = clean(str(text or ""), _SCANNER.found)
    return out if len(out) <= clip else out[:clip - 1] + "…"


def first(pattern: re.Pattern[str], text: str) -> str:
    found = pattern.search(text or "")
    return found.group(0) if found else ""


def iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return str(value or "")

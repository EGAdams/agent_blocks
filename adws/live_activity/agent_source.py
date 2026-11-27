"""IAgentEventSource over the post office's append-only events.jsonl, read from a byte offset."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import LiveEvent
from .text import REQUEST_ID, first, iso, safe

BOOTSTRAP_BYTES = 200_000
PAGE_BYTES = 400_000
STALL_SECONDS = 120.0
STATES = {"task.sent": "queued", "task.claimed": "running", "task.executing": "running", "task.progress": "running",
          "task.output": "running", "task.executed": "running", "task.retrying": "queued",
          "task.cancelled": "cancelled"}
VERDICTS = {"blocked": "blocked", "completed_without_changes": "completed without changes",
            "implemented": "implemented (not independently verified)"}


class PostOfficeEventSource:
    def __init__(self, post_office: Path, machine: str, now=lambda: datetime.now(timezone.utc),
                 stall_seconds: float = STALL_SECONDS) -> None:
        self._log = Path(post_office) / "events.jsonl"
        self._boxes = Path(post_office) / "mailboxes"
        self._machine, self._now, self._stall = machine, now, stall_seconds
        self._envelopes: dict[str, dict] = {}

    def read(self, after: int | None) -> tuple[list[LiveEvent], int]:
        try:
            size = self._log.stat().st_size
        except OSError:
            return [], 0
        start = max(0, size - BOOTSTRAP_BYTES) if after is None or after > size else after
        with open(self._log, "rb") as stream:
            stream.seek(start)
            chunk = stream.read(PAGE_BYTES)
        if after is None or after > size:
            cut = chunk.find(b"\n") + 1 if start else 0       # begin on a whole line
            start, chunk = start + cut, chunk[cut:]
        end = chunk.rfind(b"\n") + 1                          # a half-written last line is read next time
        events = [e for e in (self._event(line) for line in chunk[:end].splitlines()) if e]
        return events + self._stalled(), start + end

    def _event(self, line: bytes) -> LiveEvent | None:
        try:
            raw = json.loads(line)
            name, task, details = raw["name"], raw["task_id"], raw.get("details") or {}
        except (ValueError, KeyError, TypeError):
            return None
        envelope = self._envelope(task)
        context = envelope.get("context") or {}
        agent = str(raw.get("agent", ""))
        return LiveEvent(at=str(raw.get("at", "")), machine=self._machine,
                         agent=str(envelope.get("recipient") or agent), kind=name,
                         state=self._state(name, details), message=self._message(name, details, envelope),
                         task_id=task, bridge_task_id=str(context.get("bridge_task_id", "")),
                         request_id=first(REQUEST_ID, str(envelope.get("text", ""))),
                         adw_id=str(context.get("adw_id", "")), source="post_office/events.jsonl")

    @staticmethod
    def _state(name: str, details: dict) -> str:
        verdict = VERDICTS.get(str(details.get("verdict", "")))
        if name == "task.succeeded":
            return verdict or "succeeded"
        if name == "task.failed":
            return verdict if verdict in ("blocked", "completed without changes") else "failed"
        if name == "task.replied":
            return ""
        return STATES.get(name, "")

    def _message(self, name: str, details: dict, envelope: dict) -> str:
        if name in ("task.progress", "task.output"):
            return f"{details.get('phase', 'step')}: {safe(details.get('message'))}"
        what = name.removeprefix("task.")
        if name == "task.sent":
            return safe(f"{what}: {details.get('task_type', '')} from {envelope.get('sender', '?')} "
                        f"to {details.get('recipient', '?')}")
        extra = [f"{k}={details[k]}" for k in ("performer", "exit_code", "seconds", "attempt", "verdict", "outcome")
                 if details.get(k) not in (None, "")]
        error = str(details.get("error") or "")
        return safe(" ".join([what, *extra]) + (f" — {error}" if error else ""))

    def _envelope(self, task: str) -> dict:
        if task not in self._envelopes:
            found: dict = {}
            if task.replace("_", "").replace("-", "").isalnum():
                for path in self._boxes.glob(f"*/inbox/*/*{task}.json"):
                    try:
                        found = json.loads(path.read_text(encoding="utf-8"))
                        break
                    except (OSError, ValueError):
                        continue
            if not found:
                return {}                                     # not cached: it may be mid-move
            self._envelopes[task] = found
        return self._envelopes[task]

    def _stalled(self) -> list[LiveEvent]:
        """A task still in_process whose last recorded line is old. Said as what is known: silence."""
        open_tasks = {}
        for path in self._boxes.glob("*/inbox/in_process/*.json"):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                open_tasks[envelope["id"]] = envelope
            except (OSError, ValueError, KeyError):
                continue
        if not open_tasks:
            return []
        last: dict[str, datetime] = {}
        with open(self._log, "rb") as stream:
            stream.seek(max(0, self._log.stat().st_size - PAGE_BYTES))
            for line in stream.read().splitlines():
                try:
                    raw = json.loads(line)
                    last[raw["task_id"]] = datetime.fromisoformat(str(raw["at"]).replace("Z", "+00:00"))
                except (ValueError, KeyError, TypeError):
                    continue
        out = []
        for task, envelope in open_tasks.items():
            at = last.get(task)
            if at is None or (self._now() - at).total_seconds() < self._stall:
                continue
            context = envelope.get("context") or {}
            out.append(LiveEvent(at=iso(at), machine=self._machine, agent=str(envelope.get("recipient", "")),
                                 kind="task.stalled", state="stalled", task_id=task,
                                 message=f"no recorded activity for over {round(self._stall)}s (since this time)",
                                 bridge_task_id=str(context.get("bridge_task_id", "")),
                                 adw_id=str(context.get("adw_id", "")), source="post_office/mailboxes"))
        return out

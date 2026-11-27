"""ISupervisorEventSource over one machine's supervisor folder. Reads only; takes no lock."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import LiveEvent
from .text import BRIDGE_TASK, REQUEST_ID, first, iso, safe

TAIL_BYTES = 120_000
MAX_EVENTS = 300
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (?:\[([0-9a-f]{8})\] )?(.*)$")
ROUTINE = re.compile(r"^(watch on |mailbox \w+ on )")
BOX_STATE = {"inbox/new": "queued", "inbox/in_process": "claimed", "inbox/completed": "completed",
             "inbox/failed": "failed", "outbox": "queued", "sent": "sent", "failed": "undelivered"}


def lock_held(path: Path, proc_locks: Path = Path("/proc/locks")) -> bool | None:
    """Whether someone holds the flock, from /proc/locks: probing by locking could turn a real supervisor away."""
    try:
        inode = os.stat(path).st_ino
        table = proc_locks.read_text()
    except OSError:
        return None
    return any(line.split()[5].endswith(f":{inode}") for line in table.splitlines() if "FLOCK" in line)


class FolderSupervisorSource:
    def __init__(self, root: Path, machine: str, now=lambda: datetime.now(timezone.utc),
                 lock_probe=lock_held) -> None:
        self._root, self._machine, self._now, self._held = Path(root), machine, now, lock_probe
        self._agent = f"supervisor-{machine.lower()}"

    def read(self) -> tuple[list[LiveEvent], list[str]]:
        if not self._root.is_dir():
            return [], [f"{self._machine}: no supervisor folder; she is not installed or has never run here"]
        gaps: list[str] = []
        events = [*self._log(gaps), *self._handoffs(gaps), *self._incidents(gaps), *self._held_mail()]
        status = self._status(events, gaps)
        events.sort(key=lambda e: e.at)
        return events[-MAX_EVENTS:] + status, gaps

    def _make(self, at: str, kind: str, message: str, source: str, state: str = "", **ids) -> LiveEvent:
        text = str(message)
        ids.setdefault("request_id", first(REQUEST_ID, text))
        ids.setdefault("bridge_task_id", first(BRIDGE_TASK, text))
        return LiveEvent(at=at, machine=self._machine, agent=self._agent, kind=kind, state=state,
                         message=safe(text, 400), source=source, **ids)

    def _lines(self) -> list[tuple[str, str, str]]:
        path = self._root / "run.log"
        try:
            with open(path, "rb") as stream:
                stream.seek(max(0, path.stat().st_size - TAIL_BYTES))
                text = stream.read().decode("utf-8", "replace")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            found = LOG_LINE.match(line)
            if found:                                   # run.log is in this machine's local time
                at = datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
                out.append((iso(at), found.group(2) or "", found.group(3)))
        return out

    def _log(self, gaps: list[str]) -> list[LiveEvent]:
        lines = self._lines()
        if not lines:
            gaps.append(f"{self._machine}: no run.log lines")
        return [self._make(at, "log", text, "run.log", adw_id=adw)
                for at, adw, text in lines if not ROUTINE.match(text)][-150:]

    def _handoffs(self, gaps: list[str]) -> list[LiveEvent]:
        out = []
        for box, state in BOX_STATE.items():
            paths = sorted((self._root / "handoffs" / box).glob("*.json"),
                           key=lambda p: p.name.split("_", 1)[-1])[-30:]
            for path in paths:
                try:
                    e = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    gaps.append(f"{self._machine}: unreadable handoff {path.name}")
                    continue
                context = e.get("context") or {}
                held = context.get("not_before")
                times = [e.get(k) for k in ("completed_at", "dequeued_at", "enqueued_at", "created_at")]
                body = (f"{e.get('task_type', '?')} {e.get('kind', '')} {e.get('sender', '?')} → "
                        f"{e.get('recipient', '?')}: {str(e.get('text', ''))[:200]}")
                if e.get("error"):
                    body += f" — {str(e['error'])[:200]}"
                if held:
                    body += f" — waiting until {held}"
                shown = "failed" if str(e.get("outcome")) == "failed" else state
                out.append(self._make(next((str(t) for t in times if t), ""), f"handoff.{e.get('task_type', '?')}", body,
                                      f"handoffs/{box}", shown, task_id=str(e.get("id", "")),
                                      bridge_task_id=str(context.get("bridge_task") or first(BRIDGE_TASK, str(e.get("text", "")))),
                                      request_id=first(REQUEST_ID, str(e.get("text", "")))))
        return out

    def _incidents(self, gaps: list[str]) -> list[LiveEvent]:
        out = []
        for path in sorted((self._root / "incidents").glob("*.json"))[-15:]:
            try:
                i = json.loads(path.read_text(encoding="utf-8"))
                key, src = i["id"], f"incidents/{path.name}"
                why = "; ".join(f"{f['check']}: {f.get('detail', '')}" for f in i.get("failures", []))
                out.append(self._make(i["opened_at"], "wake", f"{key} woke for {why}", src, "open", task_id=key))
                for a in i.get("attempts", []):
                    report = a.get("report") or {}
                    text = (f"{key} rung {a.get('rung')} by {a.get('machine')}: {report.get('diagnosis', '')} "
                            f"actions: {'; '.join(report.get('actions', []))}")
                    if report.get("needs") or report.get("error"):
                        text += f" — needs: {report.get('needs') or report.get('error')}"
                    out.append(self._make(a["at"], "recovery", text, src,
                                          "fixed (rechecked)" if a.get("fixed") else "not fixed", task_id=key))
                if i.get("status") != "open":
                    out.append(self._make(i.get("closed_at") or i["opened_at"], "incident.closed",
                                          f"{key} closed {i['status']}", src, i["status"], task_id=key))
            except (OSError, ValueError, KeyError, TypeError):
                gaps.append(f"{self._machine}: unreadable incident {path.name}")
        return out

    def _held_mail(self) -> list[LiveEvent]:
        out = []
        for path in sorted((self._root / "mail_outbox").glob("*.json")):
            try:
                mail = json.loads(path.read_text(encoding="utf-8"))
                at = datetime.strptime(path.name.split("_")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except (OSError, ValueError):
                continue
            out.append(self._make(iso(at), "mail.held", f"held mail: {mail.get('subject', '')}",
                                  "mail_outbox", "held", task_id=path.stem))
        return out

    def _status(self, events: list[LiveEvent], gaps: list[str]) -> list[LiveEvent]:
        """Now: awake or asleep, the last watch heartbeat, running failure timers. Always last, never cut."""
        now = iso(self._now())
        out = []
        held = self._held(self._root / "locks" / "supervisor.lock")
        state = "unknown" if held is None else "awake" if held else "asleep"
        out.append(self._make(now, "status", f"supervisor is {state}", "locks/supervisor.lock", state))
        beats = [at for at, _, text in self._lines() if text.startswith("watch on ")]
        if beats:
            age = (self._now() - datetime.fromisoformat(beats[-1])).total_seconds()
            beat = "stale" if age > 360 else "alive"
            out.append(self._make(now, "heartbeat", f"last cron watch {round(age)}s ago ({beats[-1]})", "run.log", beat))
        else:
            gaps.append(f"{self._machine}: no watch heartbeat in the log tail")
        try:
            watch = json.loads((self._root / "watch_state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            gaps.append(f"{self._machine}: watch_state.json unreadable")
            return out
        for name, counter in (watch.get("counters") or {}).items():
            out.append(self._make(now, "timer", f"{name} failing {counter.get('fails')}x since {counter.get('since')}",
                                  "watch_state.json", "failing"))
        for name, value in (watch.get("unresolved") or {}).items():
            out.append(self._make(now, "blocker", f"{name} unresolved: {json.dumps(value)}",
                                  "watch_state.json", "escalated"))
        return out

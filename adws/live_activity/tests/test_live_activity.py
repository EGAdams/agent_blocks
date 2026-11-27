"""Offline: synthetic mailbox and supervisor folders."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(_ROOT / "adws"), str(_ROOT / "lancedb_memory")]

from live_activity import kit  # noqa: E402
from live_activity.agent_source import PostOfficeEventSource  # noqa: E402
from live_activity.supervisor_source import FolderSupervisorSource  # noqa: E402

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
TASK = "20261127T175000Z_aaaaaaaa_from_chatgpt-bridge"


def _line(name, at=NOW, **details):
    return json.dumps({"name": name, "task_id": TASK, "agent": "codex", "at": at.isoformat(), "details": details}) + "\n"


def _envelope(folder: Path, **extra):
    folder.mkdir(parents=True, exist_ok=True)
    body = {"id": TASK, "recipient": "codex", "sender": "chatgpt-bridge", "text": "see rq_c59a849c59e89020990b67e1",
            "context": {"bridge_task_id": "t_87e702eeae3da962c1068437", "adw_id": "ff7c3f8c"}, **extra}
    (folder / f"50_{TASK}.json").write_text(json.dumps(body))


def test_events_are_incremental_correlated_and_blocked_is_not_success(tmp_path):
    _envelope(tmp_path / "mailboxes" / "codex" / "inbox" / "failed")
    log = tmp_path / "events.jsonl"
    log.write_text(_line("task.claimed"))
    source = PostOfficeEventSource(tmp_path, "R46", now=lambda: NOW)
    first, cursor = source.read(None)
    assert [e.state for e in first] == ["running"]
    assert (first[0].adw_id, first[0].bridge_task_id, first[0].request_id) == (
        "ff7c3f8c", "t_87e702eeae3da962c1068437", "rq_c59a849c59e89020990b67e1")
    with log.open("a") as out:
        out.write(_line("task.failed", verdict="blocked", error="BLOCKED: dirty files"))
        out.write('{"name": "task.out')                       # half written
    second, cursor2 = source.read(cursor)
    assert [e.state for e in second] == ["blocked"] and "dirty files" in second[0].message
    assert source.read(cursor2) == ([], cursor2)


def test_a_silent_open_task_is_stalled(tmp_path):
    _envelope(tmp_path / "mailboxes" / "codex" / "inbox" / "in_process")
    (tmp_path / "events.jsonl").write_text(_line("task.claimed", at=NOW - timedelta(minutes=10)))
    events, _ = PostOfficeEventSource(tmp_path, "R46", now=lambda: NOW).read(None)
    assert events[-1].state == "stalled"


def test_credentials_never_leave(tmp_path):
    (tmp_path / "events.jsonl").write_text(_line("task.output", phase="output", message="API_TOKEN=abcdef123456789"))
    events, _ = PostOfficeEventSource(tmp_path, "R46").read(None)
    assert "abcdef" not in events[0].message and "withheld" in events[0].message


def test_supervisor_status_handoffs_incidents_and_gaps(tmp_path):
    stamp = (NOW - timedelta(minutes=1)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / "run.log").write_text(f"{stamp} [0123abcd] watch on R46\n{stamp} alert sent\n")
    (tmp_path / "watch_state.json").write_text(json.dumps(
        {"counters": {"disk": {"fails": 2, "since": "x"}}, "unresolved": {}}))
    _envelope(tmp_path / "handoffs" / "inbox" / "new", task_type="todo", created_at="2026-11-27T17:00:00Z")
    (tmp_path / "mail_outbox").mkdir()
    (tmp_path / "mail_outbox" / "20261127T170000Z_ab.json").write_text(json.dumps({"subject": "HIGH PRIORITY"}))
    events, gaps = FolderSupervisorSource(tmp_path, "R46", now=lambda: NOW, lock_probe=lambda p: False).read()
    seen = {(e.kind, e.state) for e in events}
    assert {("status", "asleep"), ("heartbeat", "alive"), ("timer", "failing"), ("handoff.todo", "queued"),
            ("mail.held", "held"), ("log", "")} <= seen
    assert not any("watch on" == e.message for e in events) and gaps == []


def test_an_absent_or_unknown_or_unreachable_machine_is_a_gap_not_silence(tmp_path):
    events, gaps = FolderSupervisorSource(tmp_path / "nope", "DAD").read()
    assert events == [] and gaps
    assert kit.read(None, "MOON").ok is False
    down = kit.remote(7, {"name": "BATI", "python": "/p", "repo": "/r", "ssh": {"host": "h"}},
                      run=lambda *a, **k: subprocess.CompletedProcess(a, 255, "", "ssh: timed out"))
    assert (down.ok, down.cursor) == (False, 7) and "timed out" in down.gaps[0]

"""The probes that watch the supervisor's own machinery: todos, held mail, ADW crashes, disk, other machines' watches.
Offline: real files under tmp_path, a fixed clock, and a stand-in for ssh."""
import json
from datetime import datetime, timedelta, timezone

from machine_supervisor import envelopes
from machine_supervisor.adapters import self_probes
from machine_supervisor.adapters.handoffs import FileHandoffBoxes
from machine_supervisor.adapters.runtime import Ran
from machine_supervisor.adapters.self_probes import AdwLogProbe, DiskProbe, HeartbeatProbe, HeldMailProbe, TodoAgeProbe
from machine_supervisor.adapters.stores import FileOutbox
from machine_supervisor.models import Check, Mail

from .fakes import catalog

START = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, at: datetime = START) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


def check(kind: str, **more) -> Check:
    return Check(name=kind, kind=kind, **more)


def test_a_todo_that_waits_too_long_fails_and_a_deferred_one_does_not(tmp_path):
    boxes, clock = FileHandoffBoxes(tmp_path / "handoffs"), Clock()
    probe = TodoAgeProbe(boxes, clock, agent_timeout=900)
    assert probe.look(check("todos")).ok
    boxes.receive(envelopes.request("chatgpt-bridge", "supervisor-dad", "todo", "x", {}, 50, START))
    deferred = {"not_before": (START + timedelta(minutes=50)).isoformat()}         # waiting out a usage limit
    boxes.receive(envelopes.request("operator", "supervisor-dad", "todo", "y", deferred, 50, START))
    boxes.receive(envelopes.request("supervisor-sqmc", "supervisor-dad", "ask", "z", {}, 50, START))   # not a todo
    clock.at = START + timedelta(minutes=29)
    assert probe.look(check("todos")).ok
    clock.at = START + timedelta(minutes=31)
    late = probe.look(check("todos"))
    assert not late.ok and late.detail.count("queued, not started") == 1 and "31 min" in late.detail
    clock.at = START + timedelta(minutes=81)
    assert probe.look(check("todos")).detail.count("queued, not started") == 2


def test_a_todo_in_process_past_the_agent_timeout_fails(tmp_path):
    boxes = FileHandoffBoxes(tmp_path / "handoffs")
    todo = envelopes.request("chatgpt-bridge", "supervisor-dad", "todo", "x", {}, 50, START)
    boxes.receive(todo)
    taken = boxes.claim(todo.id)
    since = taken.dequeued_at or taken.created_at
    clock = Clock(since + timedelta(minutes=44))                   # 15 min timeout + 30 min allowance not used up
    probe = TodoAgeProbe(boxes, clock, agent_timeout=900)
    assert probe.look(check("todos")).ok
    clock.at = since + timedelta(minutes=46)
    assert "in process for 46 min" in probe.look(check("todos")).detail


def test_held_mail_fails_only_when_the_oldest_is_older_than_allowed(tmp_path):
    outbox = FileOutbox(tmp_path / "outbox")
    stamp = (tmp_path / "outbox")
    stamp.mkdir()
    (stamp / "20261127T100000Z_aaaaaaaa.json").write_text(Mail(subject="BATI: x failed", body="b").model_dump_json())
    probe = HeldMailProbe(outbox, Clock())
    assert not probe.look(check("held-mail", minutes=60)).ok
    said = probe.look(check("held-mail", minutes=180))
    assert said.ok and "1 held, oldest 2 h 0 min: BATI: x failed" == said.detail
    assert HeldMailProbe(FileOutbox(tmp_path / "none"), Clock()).look(check("held-mail")).ok


LOG = """adw_id: 11111111
[11111111] mail --flush on SQMC
Traceback (most recent call last):
  File "x.py", line 1
ModuleNotFoundError: No module named 'pydantic'
adw_id: 22222222
[22222222] todo --work on SQMC
1 todo(s) touched
adw_id: 33333333
[33333333] supervise 20261127T173520Z-sqmc-48db9e on SQMC
incident 20261127T173520Z-sqmc-48db9e: rung 1
"""


def test_the_latest_run_of_an_adw_must_not_have_crashed(tmp_path):
    (tmp_path / "background.log").write_text(LOG)
    probe = AdwLogProbe(tmp_path)
    crashed = probe.look(check("adw-log"))
    assert not crashed.ok and "mail --flush [11111111]: ModuleNotFoundError" in crashed.detail
    assert "todo --work" not in crashed.detail and "supervise" not in crashed.detail
    with open(tmp_path / "background.log", "a") as log:          # the same ADW ran again, cleanly
        log.write("adw_id: 44444444\n[44444444] mail --flush on SQMC\n1 of 1 held mails sent\n")
    fixed = probe.look(check("adw-log"))
    assert fixed.ok and "3 ADWs" in fixed.detail
    assert AdwLogProbe(tmp_path / "nothing").look(check("adw-log")).ok


def test_disk_space_below_the_floor_fails(tmp_path):
    assert DiskProbe().look(check("disk", target=str(tmp_path), free_gb=0)).ok
    assert not DiskProbe().look(check("disk", target=str(tmp_path), free_gb=10 ** 9)).ok
    assert not DiskProbe().look(check("disk", target=str(tmp_path / "missing"))).ok


def test_another_machines_watch_must_have_run_lately(tmp_path, monkeypatch):
    table = catalog(tmp_path, me="SQMC")                          # BATI is always on, R46 is not
    answers = {}
    seen = []

    def fake_run(argv, timeout, **_):
        seen.append(argv)
        return answers[argv[-2].split("@")[-1]]                  # ssh ... user@host 'command'


    monkeypatch.setattr(self_probes, "run", fake_run)
    probe = HeartbeatProbe(table)
    answers["10.0.0.1"] = Ran(code=0, out="1795800600 1795800300\n")          # 5 minutes old
    fresh = probe.look(check("heartbeat", target="BATI", minutes=10))
    assert fresh.ok and "5 min ago" in fresh.detail and "watch_state.json" in seen[-1][-1]
    answers["10.0.0.1"] = Ran(code=0, out="1795800600 1795799400\n")          # 20 minutes old
    assert not probe.look(check("heartbeat", target="BATI", minutes=10)).ok
    answers["10.0.0.1"] = Ran(code=0, out="1795800600\n", err="stat: cannot stat")
    assert "never saved" in probe.look(check("heartbeat", target="BATI")).detail
    answers["10.0.0.1"] = Ran(code=255, err="Connection timed out")
    assert not probe.look(check("heartbeat", target="BATI")).ok
    answers["10.0.0.3"] = Ran(code=255, err="Connection timed out")
    off = probe.look(check("heartbeat", target="R46"))
    assert off.ok and "switched off" in off.detail
    assert not probe.look(check("heartbeat", target="NOPE")).ok


def test_every_check_in_the_real_table_has_a_probe_and_dad_holds_no_mail_check():
    from machine_supervisor import kit
    from pathlib import Path
    table = json.loads((Path(kit.__file__).parent / "machines.json").read_text())
    kinds = {"http", "unit", "tcp", "command", "todos", "held-mail", "adw-log", "disk", "heartbeat"}
    names = {m["name"]: [c for c in m["checks"]] for m in table["machines"]}
    assert all(c["kind"] in kinds for checks in names.values() for c in checks)
    for machine in names:
        assert {"todos", "adw-log", "disk"} <= {c["name"] for c in names[machine]}
    assert "held-mail" not in {c["name"] for c in names["DAD"]}         # no route out: it would never pass
    watched = {c["target"] for checks in names.values() for c in checks if c["kind"] == "heartbeat"}
    assert watched == {"BATI", "SQMC", "R46", "DAD"}                     # every watch is watched by another machine

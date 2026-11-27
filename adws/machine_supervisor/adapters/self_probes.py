"""IProbe for the supervisor's own machinery: what the services checks cannot see. No model in any of them.

    todos       a todo that may be worked has waited too long, or one has sat in in_process past its timeout
    held-mail   mail has been held in the outbox too long (a connector that refuses, a sign-in that expired)
    adw-log     the latest run of a supervisor ADW in background.log ended in a traceback
    disk        less free space than the check allows
    heartbeat   another machine's watch has not finished a run lately (its cron or Python is broken); a machine
                that is not always on and does not answer is switched off, not failing

Each one passes again by itself once the thing is put right, so the watch's usual rule holds: count, wake the
supervisor after `threshold` failing runs, recheck after she acts. Registered in kit.py like the other probes.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from ..interfaces import IClock, IHandoffBoxes, IMachineCatalog, IOutbox
from ..models import Check, CheckResult
from ..operations.todo import NOT_BEFORE, TASK
from .carrier import ssh_argv
from .runtime import run

HEADER = re.compile(r"^\[([0-9a-f]{8})\] (.+) on [A-Z0-9]+\s*$")
TRACEBACK = "Traceback (most recent call last)"
TAIL_BYTES = 512 * 1024
WATCH_STATE = ".local/share/machine_supervisor/watch_state.json"     # saved at the end of every watch run


def _ago(span: timedelta) -> str:
    minutes = int(span.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


class TodoAgeProbe:
    def __init__(self, boxes: IHandoffBoxes, clock: IClock, agent_timeout: int) -> None:
        self._boxes, self._clock, self._agent_timeout = boxes, clock, agent_timeout

    def look(self, check: Check) -> CheckResult:
        now, limit, late = self._clock.now(), timedelta(minutes=check.minutes), []
        for envelope in self._boxes.box("new"):
            if envelope.task_type != TASK:
                continue
            since = envelope.created_at
            try:                                     # waiting out a usage limit is not waiting too long
                since = max(since, datetime.fromisoformat(str(envelope.context[NOT_BEFORE])))
            except (KeyError, ValueError, TypeError):
                pass
            if now - since > limit:
                late.append(f"{envelope.id} queued, not started for {_ago(now - since)}")
        held = limit + timedelta(seconds=self._agent_timeout)
        for envelope in self._boxes.box("in_process"):
            since = envelope.dequeued_at or envelope.created_at
            if envelope.task_type == TASK and now - since > held:
                late.append(f"{envelope.id} in process for {_ago(now - since)}")
        if late:
            return CheckResult(check=check.name, ok=False, detail="; ".join(late)[:300])
        return CheckResult(check=check.name, ok=True, detail="no todo is waiting too long")


class HeldMailProbe:
    def __init__(self, outbox: IOutbox, clock: IClock) -> None:
        self._outbox, self._clock = outbox, clock

    def look(self, check: Check) -> CheckResult:
        held = self._outbox.pending()
        if not held:
            return CheckResult(check=check.name, ok=True, detail="no mail held")
        key, mail = held[0]                                          # oldest first: keys start with the time held
        try:
            since = datetime.strptime(key[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=self._clock.now().tzinfo)
        except ValueError:
            return CheckResult(check=check.name, ok=False, detail=f"held mail with an unreadable name: {key[:60]}")
        age = self._clock.now() - since
        said = f"{len(held)} held, oldest {_ago(age)}: {mail.subject}"[:300]
        return CheckResult(check=check.name, ok=age <= timedelta(minutes=check.minutes), detail=said)


class AdwLogProbe:
    """background.log holds the output of every ADW the supervisor starts by itself (todo --work, mail --flush,
    supervise, ...). The latest run of each one must not have crashed; an older crash with a clean run after it
    is history."""
    def __init__(self, root: Path) -> None:
        self._root = root

    def look(self, check: Check) -> CheckResult:
        path = self._root / (check.target or "background.log")
        try:
            with open(path, "rb") as stream:
                stream.seek(max(0, path.stat().st_size - TAIL_BYTES))
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return CheckResult(check=check.name, ok=True, detail=f"{path.name} not written yet")
        except OSError as error:
            return CheckResult(check=check.name, ok=False, detail=f"cannot read {path.name}: {error}"[:300])
        latest: dict[str, tuple[str, list[str]]] = {}
        current: list[str] | None = None
        for line in lines:
            found = HEADER.match(line)
            if found:
                words = found.group(2).split()
                what = " ".join([words[0], *(w for w in words[1:] if w.startswith("--"))])
                current = []
                latest[what] = (found.group(1), current)
            elif current is not None and not line.startswith("adw_id: "):    # that line opens the next run
                current.append(line)
        crashed = []
        for what, (adw_id, said) in latest.items():
            if any(TRACEBACK in line for line in said):
                last = next((line.strip() for line in reversed(said) if line.strip()), "")
                crashed.append(f"{what} [{adw_id}]: {last}")
        if crashed:
            return CheckResult(check=check.name, ok=False, detail=("latest run crashed: " + "; ".join(crashed))[:300])
        return CheckResult(check=check.name, ok=True, detail=f"latest run of each of {len(latest)} ADWs ended cleanly")


class DiskProbe:
    def look(self, check: Check) -> CheckResult:
        where = Path(check.target or "~").expanduser()
        try:
            free = shutil.disk_usage(where).free / 1e9
        except OSError as error:
            return CheckResult(check=check.name, ok=False, detail=f"cannot read {where}: {error}"[:300])
        return CheckResult(check=check.name, ok=free >= check.free_gb,
                           detail=f"{free:.1f} GB free on {where} (needs {check.free_gb:g})")


class HeartbeatProbe:
    def __init__(self, catalog: IMachineCatalog) -> None:
        self._catalog = catalog

    def look(self, check: Check) -> CheckResult:
        try:
            machine = self._catalog.get(check.target)
        except KeyError:
            return CheckResult(check=check.name, ok=False, detail=f"no machine named {check.target!r}")
        ran = run([*ssh_argv(machine), f"echo $(date +%s) $(stat -c %Y {WATCH_STATE})"], check.timeout)
        if ran.code == 255 or ran.code == 124:
            if not machine.always_on:
                return CheckResult(check=check.name, ok=True, detail=f"{machine.name} does not answer; it is not "
                                                                       "always on, so that is switched off")
            return CheckResult(check=check.name, ok=False, detail=f"{machine.name} does not answer: {ran.said}"[:300])
        numbers = ran.out.split()
        if len(numbers) != 2 or not all(n.isdigit() for n in numbers):
            return CheckResult(check=check.name, ok=False,
                               detail=f"{machine.name}'s watch has never saved its state ({ran.said})"[:300])
        age = timedelta(seconds=max(0, int(numbers[0]) - int(numbers[1])))
        return CheckResult(check=check.name, ok=age <= timedelta(minutes=check.minutes),
                           detail=f"{machine.name}'s watch last finished {_ago(age)} ago")

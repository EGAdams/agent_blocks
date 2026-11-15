"""Reading the supervisors' mailboxes and state: what the Star Trek Bridge (or a person) may look at.

Every answer is (ok, one line, data). Another machine's mailbox is read by running the same ADW there and
relaying its answer, so there is one reader and it always runs beside the files it reads.
"""
from __future__ import annotations

import json

from ..envelopes import BOXES, ID_PATTERN, last_json_line
from ..interfaces import IClock, IHandoffBoxes, ILock, IMachineCatalog, IOutbox, IProbe, IRemoteAdw, IWatchState
from ..models import Machine
from .todo import queued_todos

SCRIPT = "adw_machine_mailbox.py"
SUPERVISOR = "supervisor"
Answer = tuple[bool, str, dict]


class Mailboxes:
    def __init__(self, catalog: IMachineCatalog, boxes: IHandoffBoxes, remote: IRemoteAdw, probe: IProbe,
                 state: IWatchState, lock: ILock, outbox: IOutbox, clock: IClock) -> None:
        self._catalog, self._boxes, self._remote, self._probe = catalog, boxes, remote, probe
        self._state, self._lock, self._outbox, self._clock = state, lock, outbox, clock

    def machines(self) -> Answer:
        """The table as written. No network: whether a machine is up is `status`."""
        me = self._catalog.me()
        rows = [{"machine": m.name, "hostname": m.hostname, "agent": m.agent, "always_on": m.always_on,
                 "checks": [c.name for c in m.checks], "here": m.name == me.name} for m in [me, *self._catalog.peers()]]
        return True, f"{len(rows)} machines: {', '.join(row['machine'] for row in rows)}", {"machines": rows}

    def mailbox(self, machine: str, box: str = "all") -> Answer:
        if box != "all" and box not in BOXES:
            return False, f"no box named {box[:40]}; boxes: all, {', '.join(BOXES)}", {}
        there = self._elsewhere(machine)
        if there is not None:
            return self._relay(there, ["mailbox", "--machine", there.name, "--box", box])
        me = self._catalog.me()
        if box == "all":
            counts = {name: len(self._boxes.box(name)) for name in BOXES}
            said = ", ".join(f"{name} {count}" for name, count in counts.items() if count) or "empty"
            return True, f"{me.agent}: {said}", {"machine": me.name, "agent": me.agent, "counts": counts}
        rows = [{"id": e.id, "kind": e.kind.value, "sender": e.sender, "task_type": e.task_type,
                 "status": e.status.value, "outcome": e.outcome.value if e.outcome else None,
                 "created_at": e.created_at.isoformat(), "text": e.text[:200]} for e in self._boxes.box(box)]
        return True, f"{me.agent} {box}: {len(rows)} messages", {"machine": me.name, "box": box, "messages": rows[-50:]}

    def read(self, machine: str, envelope_id: str) -> Answer:
        if not ID_PATTERN.match(envelope_id):
            return False, f"not a handoff id: {envelope_id[:80]}", {}
        there = self._elsewhere(machine)
        if there is not None:
            return self._relay(there, ["read", "--machine", there.name, "--id", envelope_id])
        found = self._boxes.find(envelope_id)
        if found is None:
            return False, f"{self._catalog.me().agent} holds no message {envelope_id}", {}
        box, envelope = found
        return True, f"{envelope_id} is in {box}", {"machine": self._catalog.me().name, "box": box,
                                                     "message": json.loads(envelope.model_dump_json())}

    def status(self, machine: str) -> Answer:
        there = self._elsewhere(machine)
        if there is not None:
            return self._relay(there, ["status", "--machine", there.name])
        me, now, state = self._catalog.me(), self._clock.now(), self._state.load()
        results = [self._probe.look(check) for check in me.checks]
        failing = [r.check for r in results if not r.ok]
        data = {"machine": me.name, "checks": [r.model_dump() for r in results], "failing": failing,
                "supervisor_awake": self._lock.busy(SUPERVISOR), "queued_todos": queued_todos(self._boxes),
                "left_alone": {name: {"incident": held.incident, "until": held.until.isoformat()}
                               for name, held in state.unresolved.items() if held.until > now},
                "wakes_last_24h": len([at for at in state.wakes if (now - at).total_seconds() < 86400]),
                "held_mail": len(self._outbox.pending())}
        said = f"{len(results) - len(failing)}/{len(results)} checks passing"
        if failing:
            said += f"; failing: {', '.join(failing)}"
        if data["supervisor_awake"]:
            said += "; the supervisor is awake"
        return True, f"{me.name}: {said}", data

    def _elsewhere(self, machine: str) -> Machine | None:
        """The machine when it is not this one. KeyError for a name not in the table."""
        there = self._catalog.get(machine)
        return None if there.name == self._catalog.me().name else there

    def _relay(self, there: Machine, args: list[str]) -> Answer:
        ran = self._remote.run(there, SCRIPT, [*args, "--json"], None, 90)
        line = last_json_line(ran.out)
        try:
            said = json.loads(line) if line else None
        except ValueError:
            said = None
        if not isinstance(said, dict) or "ok" not in said:
            off = "" if there.always_on else " (it may be switched off, which is normal for this machine)"
            return False, f"{there.name} could not be reached{off}: {ran.said}", {"machine": there.name}
        return bool(said["ok"]), str(said.get("line", "")), said.get("data") or {}

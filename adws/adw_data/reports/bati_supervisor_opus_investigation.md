# BATI machine supervisor: investigation and repair (Opus, 2026-11-26)

Task 20261126T143233Z_d5d1a219_from_claude-code. I checked everything below with commands, mostly over
`ssh -o BatchMode=yes adamsl@100.80.49.10` (BATI, DESKTOP-SHDBATI). I did not delete, move, resend or edit
any message, did not send mail, and did not touch Sheila or pi.

## Short answer

- **The supervisor wakes and claims. It does not stall.** Each of Sheila's todos was claimed by BATI's todo worker
  within 0–6 seconds of arriving. Each one was then filed in `inbox/failed` for good, because the Claude CLI
  answered `You've hit your 5-hour limit · resets 2pm / 3:50pm (America/New_York)`. Nothing ever retries a failed
  todo, so Sheila's tasks look "never done" even though the limit had reset by 16:04 on 2026-11-25 (held mail
  went out through the same CLI then).
- **The 447 "undelivered" files are not the cause.** They are outbound alert/help/mail handoffs from BATI to
  SQMC and R46 that hit SSH timeouts. Nothing reads them when new work is claimed.

## Q1. The 447 messages (`~/.local/share/machine_supervisor/handoffs/failed`, the "undelivered" box)

- Count: 447. Sender `supervisor-bati` on all of them. 229 went to `supervisor-sqmc` and 218 to `supervisor-r46`.
- Kinds: mail 211, alert 176, help 60. Each one is still `status: queued`, `attempts: 0` (the original request,
  with the carrier's error added).
- Ages by `created_at`: 11-15: 22, 11-16: 19, 11-17: 47, 11-18: 50, 11-20: 72, 11-22: 85, 11-23: 90, 11-24: 26,
  11-25: 36. The oldest is `10_20261115T041424Z_a2022034…_to_supervisor-sqmc`. The newest is
  `20_20261125T193632Z_cf807767…_to_supervisor-r46` (mail "BATI: dashboard-via-proxy, sqmc-ssh is passing again").
- Error text, grouped:
  - 228 × `SQMC: ssh: connect to host 100.102.209.100 port 2222: Connection timed out`
  - 218 × `R46: ssh: connect to host 100.72.34.38 port 2222: Connection timed out`
  - 1 × `SQMC: timed out after 45s`
- Why they failed: of BATI's 38 incidents, 33 are `dashboard-via-proxy,sqmc-ssh` (29 unresolved). In other words
  BATI kept seeing SQMC's WSL as down, and the ladder's alert/help/mail then had to travel to SQMC, the machine
  that was down. R46 (`always_on: false`) was off. These are real network failures at the time, not a code fault.
  SQMC is reachable from BATI now (`ssh -p 2222 … true` gives exit 0).
- Delivery state: these are final. Nothing resends them (`Courier.send` moves a failed one to `failed` and raises).
  The mail they carried went through the mailer chain's fallback to `mail_outbox/`, and that fallback was flushed:
  run.log shows `held mail sent via BATI gmail` at 2026-11-25 16:04. `mail_outbox/` is empty now.

## Q2. Do they get in the way of new work?

No. The code paths:
- `TodoWorker.work` reads only `box("in_process")` and `box("new")`.
- `Watch._nudges` → `queued_todos` reads only `box("new")`.
- `Receiver.receive` → `FileHandoffBoxes.receive` checks duplicates only in the inbox boxes
  (`INBOX = new, in_process, completed, failed`), which is `inbox/failed`, not `handoffs/failed`.
- `Courier` never retries the undelivered box.

Only `adw_machine_mailbox.py mailbox` (counts) and `find(id)` look at them: the first parses 447 small JSON files
per status call, and the second runs one glob per box. That is a small cost, not interference.

## Q3. Where the path from cron wake to claim stops

Traced on BATI for `20261125T194457Z_d9fc1a3e_from_chatgpt-bridge` ("Sheila readiness", the read-only one;
the later `8d8d4aed` says "Resume … d9fc1a3e; avoid duplicate runs"), from run.log:

1. 14:44:58 `receive on BATI` → `handoff …d9fc1a3e: todo from chatgpt-bridge`. The Receiver files it in
   `inbox/new` and nudges the worker. The Bridge got back `queued`.
2. 14:44:58 `[6d1d065b] todo --work on BATI`. The worker takes the `supervisor` lock and claims it
   (`new → in_process`, attempts 1).
3. `LocalTodoAgent` → `ClaudeCli.ask` runs `claude -p`. The CLI returns `is_error` with
   `You've hit your 5-hour limit · resets 3:50pm (America/New_York)`.
4. 14:45:12 `todo …d9fc1a3e: failed -- You've hit your 5-hour limit…`. `TodoWorker._do` settles it
   `in_process → inbox/failed`. **This is where it stops.** agent_comms' lifecycle treats FAILED as final, and
   no code ever looks at `inbox/failed` again.

The same thing happened to `788e1793` (11-24 13:47:52), `d51492a0` (11-24 13:51:30) and `8d8d4aed`
(11-25 15:19:41). The r46 `ask` at 15:17:58 failed on the same limit. The cron path itself is healthy: there are
8193 "watch on BATI" lines, and cron.log's last line is `BATI: 5/5 checks passing`. `inbox/new` and
`in_process` are empty, so there is nothing pending for a wake to claim.

## Q4. SQMC and R46 (read-only)

- SQMC (this machine): 572 undelivered (288 to BATI with `port 22: Connection timed out`, 283 to R46, 1 other).
  It is the same picture with the direction reversed. `inbox/failed` has 7 `mail` handoffs from BATI that failed
  with `RuntimeError: the model never called the Gmail connector`, a separate mail-agent fault that I did not
  investigate. SQMC has no todo failed on the usage limit right now, but it runs the same code, so the same fault
  is waiting to happen there.
- R46: unreachable (`ssh: connect to host 100.72.34.38 port 2222: Connection timed out`). Not inspected.

## Root cause

`TodoWorker._do` (adws/machine_supervisor/operations/todo.py) treats any agent error as a final failure. The
Claude subscription's usage limit is a temporary "wait until X" answer, but the worker filed it as FAILED. Nothing
retries FAILED, so Sheila's todos were lost for good.

## Fix (commit 132a634, on origin/main)

- `adapters/usage_limit.py`: `ClaudeUsageLimit`, behind a new `IUsageLimit` in `interfaces.py`. It recognises
  "hit your … limit" and reads "resets 3:50pm (America/New_York)" as the next such time + 5 minutes. A reset time
  it cannot read means one hour.
- `IHandoffBoxes.release` (`in_process → new`, through agent_comms' own RUNNING→QUEUED step) and
  `IHandoffBoxes.requeue(id, why)` (`inbox/failed → new`). Requeue is used only for a todo named by id. It keeps
  the same id and file name, and keeps the old error in `context.previous_error`.
- `TodoWorker`: a limit-stopped todo goes back to `inbox/new` with `context.not_before` and
  `context.limit_deferrals`. Nothing takes it before `not_before`. The rest of the batch is not tried (it would
  hit the same limit). After 6 deferrals it is failed after all. `--work --id ID` on a todo that failed **only**
  on the usage limit puts it back in the queue and works it. Any other failure is refused with "not retried".
  Nothing picks up a failed todo on its own.
- `queued_todos(boxes, now)`: the watch counts only ready todos, so it does not start the worker every 2 minutes
  for a waiting one.
- `kit.py` wiring and DESIGN.md (one paragraph).

### Exact diff
```diff
commit 132a63470f437c42dde59e539c2c87a42f2d57e3
machine supervisor: a todo stopped by the Claude usage limit waits for the reset instead of failing

diff --git a/adws/machine_supervisor/DESIGN.md b/adws/machine_supervisor/DESIGN.md
index 06f85fa..35d049c 100644
--- a/adws/machine_supervisor/DESIGN.md
+++ b/adws/machine_supervisor/DESIGN.md
@@ -30,6 +30,12 @@ A check already passing when she wakes is closed as "recovered by itself" with n
 - **Wake limit**: `max_wakes_per_day`; after that a mail is held and the check left alone.
 - **One supervisor at a time** per machine (lock `supervisor`); the watch has its own lock.
 - Gmail has no priority field. "High priority" is a subject prefix.
+- **Usage limit is a wait, not a failure** (`adapters/usage_limit.py`, behind `IUsageLimit`): when the Claude CLI
+  says "You've hit your 5-hour limit · resets 3:50pm (...)", the todo goes back to `inbox/new` with `not_before`
+  set to the reset time (plus 5 minutes) and the watch does not start the worker for it before then; after 6 such
+  deferrals it is failed. A todo filed as failed for that reason earlier is tried again only when named:
+  `adw_machine_todo.py --work --id ID` (any other failure is refused). Added 2026-11-26 after four Sheila todos
+  on BATI were filed as failed for good on 2026-11-24/25.
 
 ## Communication (Swarm-Forge's handoffs)
 
diff --git a/adws/machine_supervisor/adapters/handoffs.py b/adws/machine_supervisor/adapters/handoffs.py
index f6d6c05..5376936 100644
--- a/adws/machine_supervisor/adapters/handoffs.py
+++ b/adws/machine_supervisor/adapters/handoffs.py
@@ -3,13 +3,15 @@
     handoffs/outbox/tmp   written here first, then renamed, so no reader ever sees half a file
     handoffs/outbox       waiting for the carrier        -> sent | failed
     handoffs/inbox/new    arrived, nobody working on it  -> in_process -> completed | failed
+                          in_process -> new again when the agent's usage limit was used up (release),
+                          and failed -> new for one todo an operator names (requeue)
 """
 from __future__ import annotations
 
 import os
 from pathlib import Path
 
-from agent_comms.models import Envelope, TaskStatus
+from agent_comms.models import Envelope, TaskStatus, utc_now
 from agent_comms.state import advance
 
 from ..envelopes import BOXES, ID_PATTERN, INBOX, filename
@@ -58,6 +60,25 @@ class FileHandoffBoxes:
         write_atomic(taken, running.model_dump_json(indent=2))
         return running
 
+    def release(self, envelope: Envelope) -> Envelope:
+        waiting = advance(envelope, TaskStatus.QUEUED)
+        self._move(envelope, "in_process", "new", waiting)
+        return waiting
+
+    def requeue(self, envelope_id: str, why: str) -> Envelope | None:
+        """The one way out of inbox/failed, which agent_comms' lifecycle treats as final: same id, same file name,
+        back in inbox/new, with the failure it had kept in its context. Written before the failed file is removed."""
+        failed = self._where(envelope_id, "failed")
+        if failed is None:
+            return None
+        old = Envelope.model_validate_json(failed.read_text(encoding="utf-8"))
+        waiting = old.model_copy(update={
+            "status": TaskStatus.QUEUED, "enqueued_at": utc_now(), "completed_at": None, "result": None,
+            "error": None, "context": {**old.context, "requeued": why[:300], "previous_error": (old.error or "")[:500]}})
+        write_atomic(self._folders["new"] / failed.name, waiting.model_dump_json(indent=2))
+        failed.unlink(missing_ok=True)
+        return waiting
+
     def settle(self, envelope: Envelope, ok: bool) -> Envelope:
         done = advance(envelope, TaskStatus.SUCCEEDED if ok else TaskStatus.FAILED)
         self._move(envelope, "in_process", "completed" if ok else "failed", done)
diff --git a/adws/machine_supervisor/adapters/usage_limit.py b/adws/machine_supervisor/adapters/usage_limit.py
new file mode 100644
index 0000000..5cd7454
--- /dev/null
+++ b/adws/machine_supervisor/adapters/usage_limit.py
@@ -0,0 +1,37 @@
+"""IUsageLimit for the Claude CLI: it answers "You've hit your 5-hour limit · resets 3:50pm (America/New_York)"
+(with is_error set) when the subscription's usage limit is used up. That is a reason to wait, not a failure.
+
+A reset time that cannot be read means: try again in an hour. A few minutes are added either way, so the next
+try does not land on the minute the limit is still in force.
+"""
+from __future__ import annotations
+
+import re
+from datetime import datetime, timedelta, timezone
+from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
+
+LIMIT = re.compile(r"hit your\b[^\n]{0,40}\blimit", re.IGNORECASE)
+RESETS = re.compile(r"resets\b[^\n]*?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:[^\n(]*\(([A-Za-z_/+-]+)\))?",
+                    re.IGNORECASE)
+MARGIN = timedelta(minutes=5)
+UNREAD = timedelta(hours=1)
+
+
+class ClaudeUsageLimit:
+    def until(self, said: str, now: datetime) -> datetime | None:
+        if not said or not LIMIT.search(said):
+            return None
+        found = RESETS.search(said)
+        if found is None:
+            return now + UNREAD
+        hour, minute, half, zone = found.groups()
+        try:
+            where = ZoneInfo(zone) if zone else timezone.utc
+        except (ZoneInfoNotFoundError, ValueError):
+            where = timezone.utc
+        hour = int(hour) % 12 + (12 if half.lower() == "pm" else 0)
+        local = now.astimezone(where)
+        reset = local.replace(hour=hour, minute=int(minute or 0), second=0, microsecond=0)
+        if reset <= local:
+            reset += timedelta(days=1)
+        return reset.astimezone(timezone.utc) + MARGIN
diff --git a/adws/machine_supervisor/interfaces.py b/adws/machine_supervisor/interfaces.py
index 56676d5..e71a46b 100644
--- a/adws/machine_supervisor/interfaces.py
+++ b/adws/machine_supervisor/interfaces.py
@@ -71,6 +71,12 @@ class ITodoAgent(Protocol):
     def work(self, envelope: Envelope) -> AgentReport: ...
 
 
+class IUsageLimit(Protocol):
+    """Reads an agent's error: when it says the subscription's usage limit is used up, the time to try again.
+    None for any other error. Never raises."""
+    def until(self, said: str, now: datetime) -> datetime | None: ...
+
+
 class IRepairAgent(Protocol):
     """A supervisor agent on one machine. Never raises: an agent that cannot be woken reports reached=False."""
     def attempt(self, incident: Incident, role: str) -> AgentReport: ...
@@ -92,6 +98,8 @@ class IHandoffBoxes(Protocol):
     def undelivered(self, envelope: Envelope, why: str) -> None: ...   # outbox -> failed
     def receive(self, envelope: Envelope) -> Envelope: ...         # into inbox/new
     def claim(self, envelope_id: str) -> Envelope | None: ...      # inbox/new -> in_process
+    def release(self, envelope: Envelope) -> Envelope: ...         # in_process -> new (to be tried again)
+    def requeue(self, envelope_id: str, why: str) -> Envelope | None: ...  # inbox/failed -> new, by name only
     def settle(self, envelope: Envelope, ok: bool) -> Envelope: ...    # in_process -> completed | failed
     def box(self, name: str) -> list[Envelope]: ...
     def find(self, envelope_id: str) -> tuple[str, Envelope] | None: ...   # (box name, envelope)
diff --git a/adws/machine_supervisor/kit.py b/adws/machine_supervisor/kit.py
index 8a20d3b..d2f1e4b 100644
--- a/adws/machine_supervisor/kit.py
+++ b/adws/machine_supervisor/kit.py
@@ -21,6 +21,7 @@ from .adapters.runtime import child_env
 from .adapters.stores import (DetachedNudge, DetachedWaker, FileIncidentStore, FileOutbox, FileRunLog, FileWatchState,
                               FlockLock, SystemClock)
 from .adapters.toast import PowerShellToast
+from .adapters.usage_limit import ClaudeUsageLimit
 from .operations.alerting import AllMachinesAlerter
 from .operations.courier import Courier
 from .operations.escalate import Escalation
@@ -78,7 +79,8 @@ class Kit:
             "ask": AskHandler(LocalAnswerer(cli, briefs, policy)), "mail": MailHandler(self.gmail),
         }, work_todos, self.clock, self.log)
         self.todo_poster = TodoPoster(self.catalog, self.courier)
-        self.todo_worker = TodoWorker(self.catalog, self.boxes, LocalTodoAgent(cli, briefs), self.lock, self.log)
+        self.todo_worker = TodoWorker(self.catalog, self.boxes, LocalTodoAgent(cli, briefs), self.lock, self.log,
+                                      ClaudeUsageLimit(), self.clock)
         self.watch = Watch(self.catalog, self.probe, self.state, self.incidents, self.lock, self.clock, waker,
                            self.outbox, flush_mail, work_todos, self.boxes, self.log)
         self.escalation = Escalation(self.catalog, self.probe, self.incidents, self.agents, self.alerter, self.mailer,
diff --git a/adws/machine_supervisor/operations/todo.py b/adws/machine_supervisor/operations/todo.py
index f039d19..72acc52 100644
--- a/adws/machine_supervisor/operations/todo.py
+++ b/adws/machine_supervisor/operations/todo.py
@@ -3,17 +3,27 @@
 TodoPoster puts one in a machine's inbox through the ordinary handoff channel. TodoWorker is the other end:
 it takes the supervisor lock, so a todo never runs beside a repair, and settles each todo's own envelope with
 what the agent reported -- the mailbox is where the answer is read.
+
+A todo the agent could not start because its usage limit was used up is not a failed todo: it goes back in
+inbox/new with `not_before` set to the reset time, and nothing takes it before then. After MAX_DEFERRALS of
+those it is failed after all. A todo already filed as failed for that reason (before this rule) is tried again
+only when somebody names it: `adw_machine_todo.py --work --id ID`.
 """
 from __future__ import annotations
 
+from datetime import datetime
+
 from agent_comms.models import Envelope, TaskStatus
 
 from ..envelopes import ID_PATTERN
 from ..errors import Undelivered
-from ..interfaces import IHandoffBoxes, ILock, IMachineCatalog, IMessenger, IRunLog, ITodoAgent
+from ..interfaces import IClock, IHandoffBoxes, ILock, IMachineCatalog, IMessenger, IRunLog, ITodoAgent, IUsageLimit
 
 TASK = "todo"
 LOCK = "supervisor"
+NOT_BEFORE = "not_before"
+DEFERRALS = "limit_deferrals"
+MAX_DEFERRALS = 6
 
 
 class TodoPoster:
@@ -39,8 +49,9 @@ class TodoPoster:
 
 class TodoWorker:
     def __init__(self, catalog: IMachineCatalog, boxes: IHandoffBoxes, agent: ITodoAgent, lock: ILock,
-                 log: IRunLog) -> None:
+                 log: IRunLog, limit: IUsageLimit, clock: IClock) -> None:
         self._catalog, self._boxes, self._agent, self._lock, self._log = catalog, boxes, agent, lock, log
+        self._limit, self._clock = limit, clock
 
     def work(self, only_id: str | None = None) -> list[str]:
         """One line per todo touched. An empty list means there was nothing to do or the supervisor is busy."""
@@ -51,28 +62,63 @@ class TodoWorker:
                 self._log.line("todo worker: the supervisor is busy; todos stay queued")
                 return []
             lines = [self._orphan(left) for left in self._boxes.box("in_process") if left.task_type == TASK]
-            waiting = [e for e in self._boxes.box("new") if e.task_type == TASK and only_id in (None, e.id)]
+            if only_id is not None:
+                lines.extend(self._requeue(only_id))
+            now = self._clock.now()
+            waiting = [e for e in self._boxes.box("new") if e.task_type == TASK
+                       and (e.id == only_id if only_id is not None else ready(e, now))]
             for queued in waiting[:self._catalog.policy().todo_batch]:
                 claimed = self._boxes.claim(queued.id)
                 if claimed is not None:
-                    lines.append(self._do(claimed))
+                    line, limited = self._do(claimed)
+                    lines.append(line)
+                    if limited:
+                        break                            # the rest would hit the same limit
             for line in lines:
                 self._log.line(line)
             return lines
 
+    def _requeue(self, envelope_id: str) -> list[str]:
+        found = self._boxes.find(envelope_id)
+        if found is None or found[0] != "failed" or found[1].task_type != TASK:
+            return []
+        if self._limit.until(found[1].error or "", self._clock.now()) is None:
+            return [f"todo {envelope_id}: not retried; it failed for a reason other than the usage limit"]
+        self._boxes.requeue(envelope_id, "failed only because the agent's usage limit was used up")
+        return [f"todo {envelope_id}: back in the queue (it failed only on the usage limit)"]
+
     def _orphan(self, left: Envelope) -> str:
         self._boxes.settle(left.model_copy(update={"error": "the worker stopped before finishing this todo",
                                                    "performer": self._catalog.me().agent}), ok=False)
         return f"todo {left.id}: failed (the worker stopped before finishing it)"
 
-    def _do(self, claimed: Envelope) -> str:
+    def _do(self, claimed: Envelope) -> tuple[str, bool]:
+        """(one line, whether the agent's usage limit stopped it)."""
         report = self._agent.work(claimed)
         ok = report.reached and not report.error
+        error = report.error or "the agent did not run"
+        until = None if ok else self._limit.until(error, self._clock.now())
+        deferrals = int(claimed.context.get(DEFERRALS) or 0)
+        if until is not None and deferrals < MAX_DEFERRALS:
+            self._boxes.release(claimed.model_copy(update={"context": {
+                **claimed.context, NOT_BEFORE: until.isoformat(), DEFERRALS: deferrals + 1}}))
+            return (f"todo {claimed.id}: usage limit, back in the queue until {until:%F %H:%M}Z -- {error[:160]}",
+                    True)
         self._boxes.settle(claimed.model_copy(update={
-            "result": report.model_dump_json(indent=2), "error": None if ok else (report.error or "the agent did not run"),
+            "result": report.model_dump_json(indent=2), "error": None if ok else error,
             "performer": self._catalog.me().agent}), ok=ok)
-        return f"todo {claimed.id}: {'done' if ok else 'failed'} -- {(report.diagnosis or report.error)[:200]}"
+        return f"todo {claimed.id}: {'done' if ok else 'failed'} -- {(report.diagnosis or report.error)[:200]}", \
+            until is not None
+
+
+def ready(envelope: Envelope, now: datetime) -> bool:
+    """False while a todo waits out a usage limit."""
+    try:
+        return datetime.fromisoformat(str(envelope.context[NOT_BEFORE])) <= now
+    except (KeyError, ValueError, TypeError):
+        return True
 
 
-def queued_todos(boxes: IHandoffBoxes) -> int:
-    return sum(1 for envelope in boxes.box("new") if envelope.task_type == TASK)
+def queued_todos(boxes: IHandoffBoxes, now: datetime | None = None) -> int:
+    """With `now`, only the todos that may be taken now (the watch's question); without, every queued one."""
+    return sum(1 for envelope in boxes.box("new") if envelope.task_type == TASK and (now is None or ready(envelope, now)))
diff --git a/adws/machine_supervisor/operations/watch.py b/adws/machine_supervisor/operations/watch.py
index e7a3d55..2206953 100644
--- a/adws/machine_supervisor/operations/watch.py
+++ b/adws/machine_supervisor/operations/watch.py
@@ -118,6 +118,6 @@ class Watch:
             self._flush.nudge()
             lines.append("held mail: trying to send it")
         # Not in a run that woke the supervisor: the todo worker could take the lock she needs first.
-        if not woke and queued_todos(self._boxes) and not self._lock.busy(SUPERVISOR):
+        if not woke and queued_todos(self._boxes, now) and not self._lock.busy(SUPERVISOR):
             self._todo_worker.nudge()
             lines.append("queued todos: worker started")
diff --git a/adws/machine_supervisor/tests/test_handoffs.py b/adws/machine_supervisor/tests/test_handoffs.py
index 6e68f0c..9c508c8 100644
--- a/adws/machine_supervisor/tests/test_handoffs.py
+++ b/adws/machine_supervisor/tests/test_handoffs.py
@@ -6,6 +6,7 @@ from agent_comms.models import TaskStatus
 
 from machine_supervisor import envelopes
 from machine_supervisor.adapters.agent import PeerRepairAgent
+from machine_supervisor.adapters.usage_limit import ClaudeUsageLimit
 from machine_supervisor.adapters.mailer import ChainMailer, PeerMailer
 from machine_supervisor.errors import Refused, Undelivered
 from machine_supervisor.models import Alert, Incident, Mail
@@ -125,7 +126,7 @@ def test_a_todo_is_queued_then_worked_and_readable_from_another_machine(sites):
     ok, line, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "check the scanner", "chatgpt-bridge", "task-1")
     assert ok and data["machine"] == "SQMC" and sqmc.nudge.nudges == 1 and queued_todos(sqmc.stores.boxes) == 1
     assert sqmc.agent.seen == []                                           # nothing ran yet
-    lines = TodoWorker(sqmc.catalog, sqmc.stores.boxes, sqmc.agent, FakeLock(), Log()).work()
+    lines = TodoWorker(sqmc.catalog, sqmc.stores.boxes, sqmc.agent, FakeLock(), Log(), ClaudeUsageLimit(), FakeClock()).work()
     assert "done" in lines[0] and queued_todos(sqmc.stores.boxes) == 0
     box, done = sqmc.stores.boxes.find(data["id"])
     assert box == "completed" and "did: check the scanner" in done.result and done.sender == "chatgpt-bridge"
@@ -143,7 +144,7 @@ def test_a_busy_supervisor_leaves_todos_queued(sites):
     TodoPoster(bati.catalog, bati.courier).post("SQMC", "later", "operator")
     lock = FakeLock()
     lock.taken.add("supervisor")
-    assert TodoWorker(sqmc.catalog, sqmc.stores.boxes, sqmc.agent, lock, Log()).work() == []
+    assert TodoWorker(sqmc.catalog, sqmc.stores.boxes, sqmc.agent, lock, Log(), ClaudeUsageLimit(), FakeClock()).work() == []
     assert queued_todos(sqmc.stores.boxes) == 1
 
 
diff --git a/adws/machine_supervisor/tests/test_usage_limit.py b/adws/machine_supervisor/tests/test_usage_limit.py
new file mode 100644
index 0000000..2d3a758
--- /dev/null
+++ b/adws/machine_supervisor/tests/test_usage_limit.py
@@ -0,0 +1,79 @@
+"""A todo the agent could not do because its subscription's usage limit was used up is put back in the queue
+until the limit resets, instead of being filed as failed for good (BATI, 2026-11-24/25: four Sheila todos)."""
+from datetime import datetime, timezone
+
+from agent_comms.models import TaskStatus
+
+from machine_supervisor.adapters.usage_limit import ClaudeUsageLimit
+from machine_supervisor.models import AgentReport
+from machine_supervisor.operations.todo import TodoPoster, TodoWorker, queued_todos
+
+from .fakes import FakeClock, FakeLock, Log
+from .test_handoffs import sites  # noqa: F401  (the fixture)
+
+LIMIT = "You've hit your 5-hour limit · resets 3:50pm (America/New_York)"
+
+
+class LimitedAgent:
+    """Says what the Claude CLI said on BATI until `limited` is turned off."""
+    def __init__(self) -> None:
+        self.limited, self.seen = True, []
+
+    def work(self, envelope) -> AgentReport:
+        self.seen.append(envelope.id)
+        if self.limited:
+            return AgentReport(machine="SQMC", role="todo", diagnosis=LIMIT, error=LIMIT)
+        return AgentReport(machine="SQMC", role="todo", diagnosis="did it")
+
+
+def worker(site, agent, clock):
+    return TodoWorker(site.catalog, site.stores.boxes, agent, FakeLock(), Log(), ClaudeUsageLimit(), clock)
+
+
+def test_the_claude_limit_message_gives_the_reset_time():
+    now = datetime(2026, 11, 25, 20, 19, tzinfo=timezone.utc)                     # 15:19 in New York
+    until = ClaudeUsageLimit().until(LIMIT, now)
+    assert until is not None and until.astimezone(timezone.utc) >= datetime(2026, 11, 25, 20, 50, tzinfo=timezone.utc)
+    assert ClaudeUsageLimit().until("the agent did not answer: ssh refused", now) is None
+
+
+def test_a_todo_that_hit_the_usage_limit_goes_back_in_the_queue_and_is_done_after_the_reset(sites):  # noqa: F811
+    bati, sqmc = sites["BATI"], sites["SQMC"]
+    ok, _, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "Sheila readiness", "chatgpt-bridge")
+    agent, clock = LimitedAgent(), FakeClock()
+    lines = worker(sqmc, agent, clock).work()
+    assert "usage limit" in lines[0]
+    box, held = sqmc.stores.boxes.find(data["id"])
+    assert box == "new" and held.status is TaskStatus.QUEUED          # before the fix: "failed", for good
+    assert queued_todos(sqmc.stores.boxes, clock.now()) == 0           # not ready yet: the watch leaves it alone
+    assert worker(sqmc, agent, clock).work() == [] and len(agent.seen) == 1
+    clock.sleep(24 * 3600)
+    agent.limited = False
+    assert queued_todos(sqmc.stores.boxes, clock.now()) == 1
+    assert "done" in worker(sqmc, agent, clock).work()[0]
+    box, done = sqmc.stores.boxes.find(data["id"])
+    assert box == "completed" and done.attempts == 2
+
+
+def test_a_todo_already_failed_by_the_limit_is_retried_only_when_named(sites):  # noqa: F811
+    bati, sqmc = sites["BATI"], sites["SQMC"]
+    _, _, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "Sheila readiness", "chatgpt-bridge")
+    claimed = sqmc.stores.boxes.claim(data["id"])                      # how BATI filed it before the fix
+    sqmc.stores.boxes.settle(claimed.model_copy(update={"error": LIMIT}), ok=False)
+    agent, clock = LimitedAgent(), FakeClock()
+    agent.limited = False
+    assert worker(sqmc, agent, clock).work() == [] and agent.seen == []    # never picked up on its own
+    lines = worker(sqmc, agent, clock).work(data["id"])
+    assert "done" in lines[-1] and agent.seen == [data["id"]]
+    assert sqmc.stores.boxes.find(data["id"])[0] == "completed"
+
+
+def test_a_todo_that_failed_for_another_reason_is_not_retried(sites):  # noqa: F811
+    bati, sqmc = sites["BATI"], sites["SQMC"]
+    _, _, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "x", "chatgpt-bridge")
+    claimed = sqmc.stores.boxes.claim(data["id"])
+    sqmc.stores.boxes.settle(claimed.model_copy(update={"error": "the agent did not run"}), ok=False)
+    agent = LimitedAgent()
+    lines = worker(sqmc, agent, FakeClock()).work(data["id"])
+    assert "not retried" in lines[0] and agent.seen == []
+    assert sqmc.stores.boxes.find(data["id"])[0] == "failed"
```

## Tests

- Before the fix: I ran the new `tests/test_usage_limit.py` against `git archive HEAD~` (c097594) with only the new
  adapter file added: `3 failed, 1 passed`. The key failure reproduces BATI's exact line:
  `assert 'usage limit' in "todo …: failed -- You've hit your 5-hour limit · resets 3:50pm (America/New_York)"`.
- After: `cd adws && uv run --quiet --with pydantic --with pytest pytest -q machine_supervisor/tests` gives
  **74 passed** (70 before, plus 4 new).
- `cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_machine_supervisor_agent` gives **all passed**.
- Smoke test: `Kit('SQMC')` built against a scratch `MACHINE_SUPERVISOR_ROOT`, and `todo_worker.work()` returned `[]`.

## Deployment

- Commit 132a634. I staged only my 9 files, by explicit path, and pushed to origin main as a fast-forward
  (`c097594..132a634`).
- **SQMC: Deployment ADW `e73a73a4`**: `ok: true, verified: true`, "already at origin/main (132a63470f)" (the
  commit was made in this tree), release `20261126T093154-d2665cdb`. All 7 health checks pass.
- **BATI: only a dry run, ADW `dbdac3e8`**: "would move agent_blocks from c0975942fc to 132a63470f (9 file(s)),
  then restart its units". **I did not run the real deploy, and stopped here as the hard rules require.** The
  `agent_blocks` project in `adws/deployment/projects.json` is written for SQMC. Its `units`/`self_units`
  (toyota/sydney/mazda/liz/yolanda receptionists, cashy-agent, agent-bridge-worker, -express) all show
  `LoadState=not-found` on BATI. `Deploy._git_pull` fast-forwards first, and then `restart()` →
  `SystemdServices.restart` raises `ServiceError` on the first missing unit. The runner turns that into a failed
  result without rolling back. BATI would be left on the new commit with the ADW saying "failed". Even without
  that, the health table (the same units, plus `~/.cache/agent_bridge/worker_health.json`, which does not exist
  on BATI) fails there. BATI's ledger already shows this: adw `04f25ffd` on 2026-11-23 reported "health checks
  fail". Fixing it means a per-machine project entry, or a BATI-specific project, in `adws/deployment/`. That is
  outside the machine_supervisor scope I was given, and it changes SQMC's deploys too, so it needs EG's or the
  orchestrator's decision.

## Verification

**Not verified live.** BATI still runs c097594, so its worker would still fail Sheila's todo for good. The fix is
proven only offline: the 4 new tests cover the deferral, the not-ready wait, retrying by name, and refusing any
other failure. I left Sheila's todos exactly where they were (`inbox/failed`, untouched).

Once 132a634 is on BATI, verify with:

    ssh adamsl@100.80.49.10 '/home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
      /home/adamsl/agent_blocks/adws/adw_machine_todo.py --work --id 20261125T194457Z_d9fc1a3e_from_chatgpt-bridge'

Expect `todo …d9fc1a3e: back in the queue (it failed only on the usage limit)` and then `…: done` or
`…: usage limit, back in the queue until …`. Read the result with
`adw_machine_mailbox.py read --machine BATI --id …`. Run it on that one id only: `788e1793`/`d51492a0` ask to
"configure Sheila executable argv and jail", and `8d8d4aed` asks to "start minimal Pi runtime". Those would touch
Sheila, so they should stay failed unless EG says otherwise.

## Left undone

1. The BATI deploy (blocked, see above), and with it the live verification on Sheila's task.
2. The `help`/`ask` handlers (`HelpHandler`, `AskHandler`) also fail outright on the usage limit (the r46 `ask` at
   2026-11-25 15:17:58). They are synchronous replies to a waiting peer, so they were left as they are.
3. SQMC's 7 failed `mail` handoffs: "the model never called the Gmail connector". A separate fault, not
   investigated.
4. The 447 (BATI) and 572 (SQMC) undelivered files are left in place as instructed. They are history only.

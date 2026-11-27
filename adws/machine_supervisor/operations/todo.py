"""Todos: work somebody (the Star Trek Bridge, EG, a peer) put in a supervisor's mailbox.

TodoPoster puts one in a machine's inbox through the ordinary handoff channel. TodoWorker is the other end:
it takes the supervisor lock, so a todo never runs beside a repair, and settles each todo's own envelope with
what the agent reported -- the mailbox is where the answer is read.

A todo the agent could not start because its usage limit was used up is not a failed todo: it goes back in
inbox/new with `not_before` set to the reset time, and nothing takes it before then. After MAX_DEFERRALS of
those it is failed after all. A todo already filed as failed for that reason (before this rule) is tried again
only when somebody names it: `adw_machine_todo.py --work --id ID`.

A todo found in inbox/in_process when the worker takes the lock was interrupted: the lock is held for the whole
of a todo, so nobody is working it. It goes back in the queue once, marked `interrupted_resumes`, and its brief
tells the agent to check what the first attempt already did before doing anything again. A second interruption
fails it -- a todo that keeps killing its worker is not tried forever.
"""
from __future__ import annotations

from datetime import datetime

from agent_comms.models import Envelope, TaskStatus

from ..envelopes import ID_PATTERN
from ..errors import Undelivered
from ..interfaces import IClock, IHandoffBoxes, ILock, IMachineCatalog, IMessenger, IRunLog, ITodoAgent, IUsageLimit

TASK = "todo"
LOCK = "supervisor"
NOT_BEFORE = "not_before"
DEFERRALS = "limit_deferrals"
MAX_DEFERRALS = 6
RESUMES = "interrupted_resumes"
MAX_RESUMES = 1


class TodoPoster:
    def __init__(self, catalog: IMachineCatalog, messenger: IMessenger) -> None:
        self._catalog, self._messenger = catalog, messenger

    def post(self, machine: str, text: str, client: str, bridge_task: str = "") -> tuple[bool, str, dict]:
        """(queued, one line, data). `queued` is true only when the receiving machine said it filed it."""
        try:
            to = self._catalog.get(machine)
        except KeyError as error:
            return False, str(error.args[0]), {}
        try:
            reply = self._messenger.send(to, TASK, text, {"bridge_task": bridge_task} if bridge_task else {},
                                         timeout=60, priority=50, sender=client)
        except Undelivered as error:
            return False, f"{to.name} could not be reached: {error}", {"machine": to.name}
        data = {"machine": to.name, "id": reply.correlation_id, "outcome": reply.outcome.value if reply.outcome else ""}
        if reply.outcome is not TaskStatus.QUEUED:
            return False, f"{to.name} refused the todo: {reply.error or reply.result or 'no reason given'}", data
        return True, f"todo queued for {to.agent} as {reply.correlation_id}", data


class TodoWorker:
    def __init__(self, catalog: IMachineCatalog, boxes: IHandoffBoxes, agent: ITodoAgent, lock: ILock,
                 log: IRunLog, limit: IUsageLimit, clock: IClock) -> None:
        self._catalog, self._boxes, self._agent, self._lock, self._log = catalog, boxes, agent, lock, log
        self._limit, self._clock = limit, clock

    def work(self, only_id: str | None = None) -> list[str]:
        """One line per todo touched. An empty list means there was nothing to do or the supervisor is busy."""
        if only_id is not None and not ID_PATTERN.match(only_id):
            return [f"not a handoff id: {only_id[:80]}"]
        with self._lock.hold(LOCK) as mine:
            if not mine:
                self._log.line("todo worker: the supervisor is busy; todos stay queued")
                return []
            lines = [self._orphan(left) for left in self._boxes.box("in_process") if left.task_type == TASK]
            if only_id is not None:
                lines.extend(self._requeue(only_id))
            now = self._clock.now()
            waiting = [e for e in self._boxes.box("new") if e.task_type == TASK
                       and (e.id == only_id if only_id is not None else ready(e, now))]
            for queued in waiting[:self._catalog.policy().todo_batch]:
                claimed = self._boxes.claim(queued.id)
                if claimed is not None:
                    line, limited = self._do(claimed)
                    lines.append(line)
                    if limited:
                        break                            # the rest would hit the same limit
            for line in lines:
                self._log.line(line)
            return lines

    def _requeue(self, envelope_id: str) -> list[str]:
        found = self._boxes.find(envelope_id)
        if found is None or found[0] != "failed" or found[1].task_type != TASK:
            return []
        if self._limit.until(found[1].error or "", self._clock.now()) is None:
            return [f"todo {envelope_id}: not retried; it failed for a reason other than the usage limit"]
        self._boxes.requeue(envelope_id, "failed only because the agent's usage limit was used up")
        return [f"todo {envelope_id}: back in the queue (it failed only on the usage limit)"]

    def _orphan(self, left: Envelope) -> str:
        resumes = int(left.context.get(RESUMES) or 0)
        if resumes < MAX_RESUMES:
            self._boxes.release(left.model_copy(update={"context": {
                **left.context, RESUMES: resumes + 1, "interrupted_at": self._clock.now().isoformat()}}))
            return f"todo {left.id}: interrupted; back in the queue to resume ({resumes + 1} of {MAX_RESUMES})"
        self._boxes.settle(left.model_copy(update={
            "error": f"the worker stopped before finishing this todo, again after {resumes} resume(s)",
            "performer": self._catalog.me().agent}), ok=False)
        return f"todo {left.id}: failed (the worker stopped before finishing it, again after resuming)"

    def _do(self, claimed: Envelope) -> tuple[str, bool]:
        """(one line, whether the agent's usage limit stopped it)."""
        report = self._agent.work(claimed)
        ok = report.reached and not report.error
        error = report.error or "the agent did not run"
        until = None if ok else self._limit.until(error, self._clock.now())
        deferrals = int(claimed.context.get(DEFERRALS) or 0)
        if until is not None and deferrals < MAX_DEFERRALS:
            self._boxes.release(claimed.model_copy(update={"context": {
                **claimed.context, NOT_BEFORE: until.isoformat(), DEFERRALS: deferrals + 1}}))
            return (f"todo {claimed.id}: usage limit, back in the queue until {until:%F %H:%M}Z -- {error[:160]}",
                    True)
        self._boxes.settle(claimed.model_copy(update={
            "result": report.model_dump_json(indent=2), "error": None if ok else error,
            "performer": self._catalog.me().agent}), ok=ok)
        return f"todo {claimed.id}: {'done' if ok else 'failed'} -- {(report.diagnosis or report.error)[:200]}", \
            until is not None


def ready(envelope: Envelope, now: datetime) -> bool:
    """False while a todo waits out a usage limit."""
    try:
        return datetime.fromisoformat(str(envelope.context[NOT_BEFORE])) <= now
    except (KeyError, ValueError, TypeError):
        return True


def interrupted_todos(boxes: IHandoffBoxes) -> int:
    """Todos in inbox/in_process. Read while the supervisor lock is free, each is one the worker left behind."""
    return sum(1 for envelope in boxes.box("in_process") if envelope.task_type == TASK)


def queued_todos(boxes: IHandoffBoxes, now: datetime | None = None) -> int:
    """With `now`, only the todos that may be taken now (the watch's question); without, every queued one."""
    return sum(1 for envelope in boxes.box("new") if envelope.task_type == TASK and (now is None or ready(envelope, now)))

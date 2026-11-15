"""Todos: work somebody (the Star Trek Bridge, EG, a peer) put in a supervisor's mailbox.

TodoPoster puts one in a machine's inbox through the ordinary handoff channel. TodoWorker is the other end:
it takes the supervisor lock, so a todo never runs beside a repair, and settles each todo's own envelope with
what the agent reported -- the mailbox is where the answer is read.
"""
from __future__ import annotations

from agent_comms.models import Envelope, TaskStatus

from ..envelopes import ID_PATTERN
from ..errors import Undelivered
from ..interfaces import IHandoffBoxes, ILock, IMachineCatalog, IMessenger, IRunLog, ITodoAgent

TASK = "todo"
LOCK = "supervisor"


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
                 log: IRunLog) -> None:
        self._catalog, self._boxes, self._agent, self._lock, self._log = catalog, boxes, agent, lock, log

    def work(self, only_id: str | None = None) -> list[str]:
        """One line per todo touched. An empty list means there was nothing to do or the supervisor is busy."""
        if only_id is not None and not ID_PATTERN.match(only_id):
            return [f"not a handoff id: {only_id[:80]}"]
        with self._lock.hold(LOCK) as mine:
            if not mine:
                self._log.line("todo worker: the supervisor is busy; todos stay queued")
                return []
            lines = [self._orphan(left) for left in self._boxes.box("in_process") if left.task_type == TASK]
            waiting = [e for e in self._boxes.box("new") if e.task_type == TASK and only_id in (None, e.id)]
            for queued in waiting[:self._catalog.policy().todo_batch]:
                claimed = self._boxes.claim(queued.id)
                if claimed is not None:
                    lines.append(self._do(claimed))
            for line in lines:
                self._log.line(line)
            return lines

    def _orphan(self, left: Envelope) -> str:
        self._boxes.settle(left.model_copy(update={"error": "the worker stopped before finishing this todo",
                                                   "performer": self._catalog.me().agent}), ok=False)
        return f"todo {left.id}: failed (the worker stopped before finishing it)"

    def _do(self, claimed: Envelope) -> str:
        report = self._agent.work(claimed)
        ok = report.reached and not report.error
        self._boxes.settle(claimed.model_copy(update={
            "result": report.model_dump_json(indent=2), "error": None if ok else (report.error or "the agent did not run"),
            "performer": self._catalog.me().agent}), ok=ok)
        return f"todo {claimed.id}: {'done' if ok else 'failed'} -- {(report.diagnosis or report.error)[:200]}"


def queued_todos(boxes: IHandoffBoxes) -> int:
    return sum(1 for envelope in boxes.box("new") if envelope.task_type == TASK)

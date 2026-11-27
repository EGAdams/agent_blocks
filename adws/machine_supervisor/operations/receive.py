"""One handoff arriving at this machine: checked, filed in the inbox, worked, settled, and answered.

A `todo` is the exception: it is filed and left in inbox/new for the todo worker, and answered at once as
queued. A todo for a Bridge task this machine already filed (same sender, same `bridge_task`) is not filed again:
the reply names the first one as `filed_as`, so a reassigned or resent Bridge task can never run the work twice. Who may send what is a consistency check, not authentication -- the SSH key that reached this
machine is the authentication.
"""
from __future__ import annotations

from agent_comms.models import Envelope, MessageKind, TaskStatus

from .. import envelopes
from ..errors import Refused
from ..interfaces import IClock, IHandoffBoxes, IHandoffHandler, IMachineCatalog, INudge, IRunLog

PEER_ONLY = ("alert", "help", "ask", "mail")
DEFERRED = "todo"


class Receiver:
    def __init__(self, catalog: IMachineCatalog, boxes: IHandoffBoxes, handlers: dict[str, IHandoffHandler],
                 todo_worker: INudge, clock: IClock, log: IRunLog) -> None:
        self._catalog, self._boxes, self._handlers = catalog, boxes, handlers
        self._todo_worker, self._clock, self._log = todo_worker, clock, log

    def receive(self, raw: str) -> Envelope:
        """Returns the reply. Raises Refused only for text that is not a handoff addressed to this machine."""
        me = self._catalog.me().agent
        try:
            asked = Envelope.model_validate_json(raw)
        except ValueError as error:
            raise Refused(f"not a handoff: {str(error)[:200]}") from None
        if asked.recipient != me or asked.kind is not MessageKind.REQUEST or not envelopes.ID_PATTERN.match(asked.id) \
                or not envelopes.NAME_PATTERN.match(asked.sender):
            raise Refused(f"not a request for {me}")
        again = self._already_filed(asked)
        if again is not None:
            box, first = again
            self._log.line(f"handoff {asked.id}: same Bridge task as {first.id} ({box}); not filed again")
            return self._reply(asked, TaskStatus.QUEUED, f"already filed for {me} as {first.id}, now in {box}; "
                                                         "not filed again", None, filed_as=first.id)
        asked = self._boxes.receive(asked.model_copy(update={"trace": [*asked.trace, me], "status": TaskStatus.QUEUED,
                                                             "attempts": 0}))
        self._log.line(f"handoff {asked.id}: {asked.task_type} from {asked.sender}")
        why = self._why_not(asked)
        if why is None and asked.task_type == DEFERRED:
            self._todo_worker.nudge()
            return self._reply(asked, TaskStatus.QUEUED, f"queued for {me} as {asked.id}", None)
        claimed = self._boxes.claim(asked.id)
        if claimed is None:
            raise Refused(f"handoff {asked.id} was taken by somebody else")
        result, error = None, why
        if why is None:
            try:
                result = self._handlers[asked.task_type].handle(claimed)
            except Exception as failure:                                   # a handler's failure is a failed reply
                error = f"{type(failure).__name__}: {failure}"[:1000]
        done = self._boxes.settle(claimed.model_copy(update={"result": result, "error": error, "performer": me}),
                                  ok=error is None)
        self._log.line(f"handoff {asked.id}: {done.status.value}" + (f" ({error})" if error else ""))
        return self._reply(asked, done.status, result, error)

    def _why_not(self, asked: Envelope) -> str | None:
        peer = self._catalog.by_agent(asked.sender)
        is_peer = peer is not None and peer.name != self._catalog.me().name
        if asked.task_type in PEER_ONLY and asked.task_type in self._handlers:
            return None if is_peer else f"{asked.task_type} is accepted only from another machine's supervisor"
        if asked.task_type == DEFERRED:
            allowed = is_peer or asked.sender in self._catalog.policy().todo_senders
            return None if allowed else f"{asked.sender} may not give this supervisor a todo"
        return f"unknown task type '{asked.task_type[:40]}'"

    def _already_filed(self, asked: Envelope) -> tuple[str, Envelope] | None:
        task = str(asked.context.get("bridge_task") or "")
        if asked.task_type != DEFERRED or not task:
            return None
        for box in ("new", "in_process", "completed", "failed"):
            for held in self._boxes.box(box):
                if held.task_type == DEFERRED and held.sender == asked.sender \
                        and str(held.context.get("bridge_task") or "") == task:
                    return box, held
        return None

    def _reply(self, asked: Envelope, outcome: TaskStatus, result: str | None, error: str | None,
               filed_as: str | None = None) -> Envelope:
        reply = envelopes.reply_to(asked, self._catalog.me().agent, outcome, result, error, self._clock.now())
        if filed_as is not None:
            reply = reply.model_copy(update={"context": {**reply.context, "filed_as": filed_as}})
        self._boxes.post(reply)
        self._boxes.sent(reply)              # the carrier's caller is reading it off this ADW's output right now
        return reply

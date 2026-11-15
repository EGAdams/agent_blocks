"""IMessenger: one handoff out through the outbox, its reply in through the inbox. Both are kept on disk."""
from __future__ import annotations

from agent_comms.models import Envelope, MessageKind

from .. import envelopes
from ..errors import Refused, Undelivered
from ..interfaces import ICarrier, IClock, IHandoffBoxes, IMachineCatalog
from ..models import Machine


class Courier:
    def __init__(self, catalog: IMachineCatalog, boxes: IHandoffBoxes, carrier: ICarrier, clock: IClock) -> None:
        self._catalog, self._boxes, self._carrier, self._clock = catalog, boxes, carrier, clock

    def send(self, to: Machine, task_type: str, text: str, context: dict, timeout: int,
             priority: int = 50, sender: str | None = None) -> Envelope:
        asked = envelopes.request(sender or self._catalog.me().agent, to.agent, task_type, text, context, priority,
                                  self._clock.now())
        self._boxes.post(asked)
        try:
            reply = self._carrier.carry(to, asked, timeout)
            if reply.kind is not MessageKind.REPLY or reply.correlation_id != asked.id:
                raise Undelivered(f"{to.name} answered a different handoff")
        except Undelivered as error:
            self._boxes.undelivered(asked, str(error))
            raise
        self._boxes.sent(asked)
        try:                                             # the reply is filed like any other arrival
            self._boxes.receive(reply)
            claimed = self._boxes.claim(reply.id)
            if claimed is not None:
                self._boxes.settle(claimed, ok=True)
        except Refused:
            pass                                         # a reply id we already hold: nothing more to file
        return reply

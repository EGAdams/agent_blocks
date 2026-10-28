"""MailboxReplyWaiter -- IRequestReply over the sender's own inbox.

A reply is an Envelope of kind REPLY whose correlation id is the request's
id, delivered to the original sender's `inbox/new`. Waiting is polling for
it; taking it moves it to `completed`, so the same reply is never consumed
twice. Replies nobody waited for stay in `new` until the sender's board or a
later wait picks them up.
"""
from __future__ import annotations

from .models import Envelope, MessageKind
from .ports import IClock, IMessageTransport


class MailboxReplyWaiter:

    def __init__( self, transport: IMessageTransport, clock: IClock, poll_seconds: float = 0.5 ) -> None:
        self._transport = transport
        self._clock     = clock
        self._poll      = poll_seconds

    def await_reply( self, sender: str, correlation_id: str, timeout_seconds: float ) -> Envelope | None:
        deadline = self._clock.monotonic() + timeout_seconds
        while True:
            reply = self.take( sender, correlation_id )
            if reply is not None:
                return reply
            if self._clock.monotonic() >= deadline:
                return None
            self._clock.sleep( self._poll )

    def take( self, sender: str, correlation_id: str ) -> Envelope | None:
        for envelope in self._transport.box( sender, "new" ):
            if envelope.kind is MessageKind.REPLY and envelope.correlation_id == correlation_id:
                claimed = self._transport.claim( sender, envelope.id )
                if claimed is not None:
                    return self._transport.complete( sender, claimed )
        return None

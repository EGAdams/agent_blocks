"""MailboxTaskBoard and TaskCanceller -- the shared view of the post office.

The board is read-only: who asked whom for what, where each task is now, and
how it went. It reads the mailboxes themselves, so it is never out of step
with them (Swarm-Forge's rule: file location is the state).
"""
from __future__ import annotations

from .models import Envelope, MessageKind, TaskEvent, TaskStatus
from .ports import IEventPublisher, IMessageTransport


class MailboxTaskBoard:
    """ITaskBoard."""

    def __init__( self, transport: IMessageTransport ) -> None:
        self._transport = transport

    def tasks_from( self, sender: str ) -> list[ Envelope ]:
        current = []
        for sent in self._transport.box( sender, "sent" ):
            if sent.kind is MessageKind.REQUEST:
                current.append( self.task( sent.id ) or sent )
        return current

    def task( self, task_id: str ) -> Envelope | None:
        found = self._transport.find( task_id )
        return found[ 2 ] if found else None


class TaskCanceller:
    """Withdraws a queued task and tells its sender. A running one is left alone."""

    def __init__( self, transport: IMessageTransport, events: IEventPublisher ) -> None:
        self._transport = transport
        self._events    = events

    def cancel( self, task_id: str, by: str ) -> str:
        found = self._transport.find( task_id )
        if found is None:
            return f"There's no task { task_id }."
        agent, box, envelope = found
        if envelope.status is not TaskStatus.QUEUED:
            return f"Task { task_id } is already { envelope.status.value }, so it can't be cancelled."
        cancelled = self._transport.cancel( agent, task_id )
        if cancelled is None:
            return f"Task { task_id } was picked up just now, so it can't be cancelled."
        self._events.publish( TaskEvent( name="task.cancelled", task_id=task_id, agent=by ))
        return f"Cancelled task { task_id } before { agent } started it."

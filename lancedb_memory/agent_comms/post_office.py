"""PostOffice -- the Mediator every agent sends through.

No agent holds a reference to another. A sender hands the post office a
TaskRequest; the post office validates it, addresses an Envelope, drops it in
the recipient's mailbox, tells the observers, wakes a worker for the
recipient, and -- only if the caller asked to wait -- waits for the reply.
That one path serves all three communication styles:

* synchronous request/response: `wait_seconds > 0`, the receipt carries the reply;
* asynchronous delegation:     `wait_seconds == 0`, the receipt carries the task id,
                                and the reply lands in the sender's inbox later;
* shared coordination:          everything is on the board (ITaskBoard) and in the
                                event log, for whoever subscribes.

A wait that times out loses nothing: the task keeps running, and its reply is
delivered to the sender's inbox like any asynchronous one. Nothing is retried
on the sender's side, so a non-idempotent build is never posted twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .models import Envelope, MessageKind, SendReceipt, TaskEvent, TaskRequest, TaskStatus
from .ports import (
    IAgentRegistry, IEventPublisher, IMessageTransport, IRequestReply, IRequestValidator, IWorkerLauncher,
)


def new_task_id( sender: str ) -> str:
    """Swarm-Forge's id shape: `<utc timestamp>_<unique>_from_<sender>`."""
    stamp = datetime.now( timezone.utc ).strftime( "%Y%m%dT%H%M%SZ" )
    return f"{ stamp }_{ uuid.uuid4().hex[ :8 ] }_from_{ sender }"


class PostOffice:
    """ITaskDispatcher."""

    def __init__( self,
                  registry:  IAgentRegistry,
                  validator: IRequestValidator,
                  transport: IMessageTransport,
                  events:    IEventPublisher,
                  launcher:  IWorkerLauncher,
                  replies:   IRequestReply ) -> None:
        self._registry  = registry
        self._validator = validator
        self._transport = transport
        self._events    = events
        self._launcher  = launcher
        self._replies   = replies

    def send( self, request: TaskRequest ) -> SendReceipt:
        problem = self._validator.problem( request )
        if problem:
            self._events.publish( TaskEvent( name="task.refused", task_id="-", agent=request.sender,
                                             details={ "recipient": request.recipient,
                                                       "task_type": request.task_type, "reason": problem }))
            return SendReceipt( accepted=False, reason=problem )

        envelope = self._transport.deliver( self._address( request ))
        self._events.publish( TaskEvent( name="task.sent", task_id=envelope.id, agent=request.sender,
                                         details={ "recipient": envelope.recipient,
                                                   "task_type": envelope.task_type,
                                                   "priority":  envelope.priority }))
        self._launcher.wake( envelope.recipient )

        wait = self._wait_seconds( request )
        if wait <= 0:
            return SendReceipt( accepted=True, task_id=envelope.id )
        reply = self._replies.await_reply( request.sender, envelope.id, wait )
        if reply is None:
            self._events.publish( TaskEvent( name="task.wait_timed_out", task_id=envelope.id,
                                             agent=request.sender, details={ "waited_seconds": wait }))
        return SendReceipt( accepted=True, task_id=envelope.id, waited=True, reply=reply )

    # -- internals -----------------------------------------------------------

    def _address( self, request: TaskRequest ) -> Envelope:
        card       = self._registry.card( request.recipient )
        capability = card.capability( request.task_type ) if card else None
        parent     = self._parent( request )
        task_id    = new_task_id( request.sender )
        idempotent = capability.idempotent if capability else True
        return Envelope(
            id             = task_id,
            kind           = MessageKind.REQUEST,
            sender         = request.sender,
            recipient      = request.recipient,
            # The card's own name for the work, so "fix" sent to the mechanic
            # arrives as its "repair".
            task_type      = capability.task_type if capability else request.task_type,
            text           = request.text,
            context        = request.context,
            priority       = request.priority,
            correlation_id = task_id,
            parent_id      = parent.id if parent else request.parent_id,
            # A parent's trace already ends with its own sender.
            trace          = [ *( parent.trace if parent else []), request.sender ],
            idempotent     = idempotent,
            # Only work that is safe to repeat is ever repeated.
            max_attempts   = 3 if idempotent else 1,
        )

    def _parent( self, request: TaskRequest ) -> Envelope | None:
        """The task the sender is working on right now, if exactly one -- so a
        request made while answering another carries its trace along."""
        running = [ e for e in self._transport.box( request.sender, "in_process" )
                    if e.status is TaskStatus.RUNNING and e.kind is MessageKind.REQUEST ]
        if request.parent_id:
            return next(( e for e in running if e.id == request.parent_id ), None )
        return running[ 0 ] if len( running ) == 1 else None

    def _wait_seconds( self, request: TaskRequest ) -> float:
        if request.wait_seconds is not None:
            return request.wait_seconds
        card       = self._registry.card( request.recipient )
        capability = card.capability( request.task_type ) if card else None
        return capability.default_wait_seconds if capability else 0.0

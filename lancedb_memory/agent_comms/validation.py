"""IRequestValidator rules, one reason to refuse each, and the chain over them.

These are the only refusals the post office makes. Every one names a real
fact -- the agent isn't registered, its card doesn't list that work, it
doesn't take requests from this sender, the request would loop -- and says so
in words a receptionist can repeat. None of them second-guesses what a
capable agent may do: if a card says an agent builds, a build request goes
through. That is the fix for Sydney's refusal, where a dispatcher's prompt
decided a task "wasn't a question" and nothing was ever sent.
"""
from __future__ import annotations

from .models import TaskRequest, TaskStatus
from .ports import IAgentRegistry, IMessageTransport, IRequestValidator


class ValidationChain:
    """IRequestValidator Composite: the first rule with a problem wins."""

    def __init__( self, rules: list[ IRequestValidator ]) -> None:
        self._rules = list( rules )

    def problem( self, request: TaskRequest ) -> str | None:
        for rule in self._rules:
            reason = rule.problem( request )
            if reason:
                return reason
        return None


class RegisteredParties:
    """Both ends must be registered agents -- that is what "authorised" means here."""

    def __init__( self, registry: IAgentRegistry ) -> None:
        self._registry = registry

    def problem( self, request: TaskRequest ) -> str | None:
        if self._registry.card( request.sender ) is None:
            return f"{ request.sender } isn't registered with the post office, so it can't send work."
        if self._registry.card( request.recipient ) is None:
            known = ", ".join( self._registry.names())
            return f"There's no agent called { request.recipient }. Registered agents: { known }."
        if request.sender == request.recipient:
            return f"{ request.recipient } can't send a task to itself."
        return None


class AcceptsSender:
    def __init__( self, registry: IAgentRegistry ) -> None:
        self._registry = registry

    def problem( self, request: TaskRequest ) -> str | None:
        card = self._registry.card( request.recipient )
        if card is None or "*" in card.accepts_from or request.sender in card.accepts_from:
            return None
        return f"{ card.display_name } only takes work from { ', '.join( card.accepts_from ) }."


class HasCapability:
    """The recipient's card must list the work. The card is the authority."""

    def __init__( self, registry: IAgentRegistry ) -> None:
        self._registry = registry

    def problem( self, request: TaskRequest ) -> str | None:
        card = self._registry.card( request.recipient )
        if card is None or card.capability( request.task_type ) is not None:
            return None
        can = ", ".join( c.task_type for c in card.capabilities ) or "nothing yet"
        return f"{ card.display_name } isn't set up for { request.task_type } work. It can do: { can }."


class NoLoopBack:
    """Refuse A -> B while A is working on something B asked for.

    Toyota answering a question from Sydney must not post a new request back
    to Sydney, who is still waiting on Toyota: each would wait on the other
    until both timed out.
    """

    def __init__( self, transport: IMessageTransport ) -> None:
        self._transport = transport

    def problem( self, request: TaskRequest ) -> str | None:
        for running in self._transport.box( request.sender, "in_process" ):
            if running.status is TaskStatus.RUNNING and \
               ( running.sender == request.recipient or request.recipient in running.trace ):
                return ( f"{ request.recipient } is waiting on { request.sender } for task { running.id }; "
                         "sending work back would loop. Answer that task instead." )
        return None

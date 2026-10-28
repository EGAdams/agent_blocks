"""The post office's interfaces. Protocols only -- no concrete import, no SDK.

One responsibility each, so a caller depends on exactly the slice it uses:
a sender needs `ITaskDispatcher`, never the mailbox; a worker needs
`IMessageTransport` and an `ITaskExecutor`, never the registry's file format;
the mechanic needs `IAgentDiagnostic`, never the event log's layout.

`factory.py` is the only module that names the concrete classes behind these.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from .models import AgentCard, Alert, Diagnosis, Envelope, SendReceipt, TaskEvent, TaskRequest, TaskResult


# -- Discovery ---------------------------------------------------------------

class IAgentRegistry( Protocol ):
    """Who is registered, and what each one can really do."""

    def names( self ) -> list[ str ]: ...

    def card( self, name: str ) -> AgentCard | None: ...


# -- Routing -----------------------------------------------------------------

class IMessageRouter( Protocol ):
    """Turns free text ("have Sonnet build ...") into an addressed request."""

    def route( self, sender: str, text: str ) -> TaskRequest | None: ...


class IRequestValidator( Protocol ):
    """Why a request must not be sent, or None when it may.

    Validators are rules about the *request* -- known recipient, real
    capability, authorised sender, no loop. They never decide what an agent's
    tools allow; the capability card is the single statement of that.
    """

    def problem( self, request: TaskRequest ) -> str | None: ...


# -- Transport ---------------------------------------------------------------

class IMessageTransport( Protocol ):
    """Durable per-agent mailboxes. Delivery is a file move, as in Swarm-Forge."""

    def deliver( self, envelope: Envelope ) -> Envelope: ...

    def claim_next( self, agent: str ) -> Envelope | None: ...

    def claim( self, agent: str, envelope_id: str ) -> Envelope | None: ...

    def complete( self, agent: str, envelope: Envelope ) -> Envelope: ...

    def fail( self, agent: str, envelope: Envelope ) -> Envelope: ...

    def requeue( self, agent: str, envelope: Envelope ) -> Envelope: ...

    def cancel( self, agent: str, envelope_id: str ) -> Envelope | None: ...

    def find( self, envelope_id: str ) -> tuple[ str, str, Envelope ] | None:
        """( agent, box, envelope ) for an id anywhere in the post office."""
        ...

    def box( self, agent: str, box: str ) -> list[ Envelope ]: ...


# -- Execution ---------------------------------------------------------------

class ITaskExecutor( Protocol ):
    """Normalises one agent runtime: request in, result out. The Adapter role.

    Implementations never raise for a failed piece of work -- a failure is a
    TaskResult( ok=False ). They raise only when they could not even try.
    """

    @property
    def performer( self ) -> str: ...

    async def execute( self, envelope: Envelope ) -> TaskResult: ...


class IExecutorFactory( Protocol ):
    """Builds the executor for one registered agent. The Abstract Factory role."""

    def executor_for( self, card: AgentCard ) -> ITaskExecutor: ...


class IWorkerLauncher( Protocol ):
    """Makes sure somebody will work an agent's inbox. Never blocks."""

    def wake( self, agent: str ) -> None: ...


# -- Coordination ------------------------------------------------------------

class ITaskDispatcher( Protocol ):
    """Validate, address and post one request; optionally wait for its reply."""

    def send( self, request: TaskRequest ) -> SendReceipt: ...


class IRequestReply( Protocol ):
    """Wait for the reply to one request, by correlation id."""

    def await_reply( self, sender: str, correlation_id: str, timeout_seconds: float ) -> Envelope | None: ...


class ITaskBoard( Protocol ):
    """The shared view of who has what, and how it went."""

    def tasks_from( self, sender: str ) -> list[ Envelope ]: ...

    def task( self, task_id: str ) -> Envelope | None: ...


# -- Observation -------------------------------------------------------------

class IEventSubscriber( Protocol ):
    def notify( self, event: TaskEvent ) -> None: ...


class IEventPublisher( Protocol ):
    def publish( self, event: TaskEvent ) -> None: ...


class IEventLog( Protocol ):
    """Reads back what was published, for the board and the mechanic."""

    def events_for( self, task_id: str ) -> list[ TaskEvent ]: ...


class IClock( Protocol ):
    def monotonic( self ) -> float: ...

    def sleep( self, seconds: float ) -> None: ...


# -- Failure and diagnosis ---------------------------------------------------

class IRetryPolicy( Protocol ):
    """Whether a failed attempt may run again."""

    def should_retry( self, envelope: Envelope, result: TaskResult ) -> bool: ...


class IAgentDiagnostic( Protocol ):
    """Everything known about one task, for the mechanic."""

    def diagnose( self, task_id: str, adw_id: str | None = None ) -> Diagnosis:
        """`adw_id` covers a failure the post office never carried: an ADW run."""
        ...


class ICaseHistory( Protocol ):
    """Failures and their corrections, kept so the next one is quicker."""

    def record( self, diagnosis: Diagnosis, correction: str = "" ) -> str: ...

    def similar( self, error: str ) -> list[ str ]: ...


# -- Alerts ------------------------------------------------------------------

class IAlertBoard( Protocol ):
    """Failures waiting for the agent who asked for the work, until acknowledged."""

    def raise_alert( self, alert: Alert ) -> None: ...

    def pending( self, agent: str ) -> list[ Alert ]: ...

    def acknowledge( self, agent: str, alert_ids: list[ str ] | None = None ) -> int:
        """None acknowledges everything pending; returns how many were."""
        ...


class ITaskLiveness( Protocol ):
    """Whether a claimed task still has a living worker behind it."""

    def hold( self, task_id: str ) -> AbstractContextManager[ None ]:
        """Held by the worker for as long as it executes the task."""
        ...

    def is_alive( self, task_id: str ) -> bool: ...

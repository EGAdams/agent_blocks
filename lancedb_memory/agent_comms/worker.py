"""AgentWorker -- works one agent's inbox, one task per call.

Swarm-Forge's receive loop (`ready_for_next` -> do the work ->
`done_with_current`) as one object: claim the next request, hand it to the
agent's ITaskExecutor, then settle it -- completed with a reply, requeued for
another attempt, or failed with a failure reply and a `task.failed` event the
mechanic hears.

The worker never decides what the agent can do. It runs what was delivered.
"""
from __future__ import annotations

from datetime import timedelta

from .models import Envelope, MessageKind, TaskEvent, TaskResult, TaskStatus, utc_now
from .adapters.liveness import NoLiveness
from .ports import IEventPublisher, IMessageTransport, IRetryPolicy, ITaskExecutor, ITaskLiveness


class IdempotentRetries:
    """IRetryPolicy: retry only what is safe to repeat, up to its own limit."""

    def should_retry( self, envelope: Envelope, result: TaskResult ) -> bool:
        return envelope.idempotent and envelope.attempts < envelope.max_attempts


class NeverRetry:
    def should_retry( self, envelope: Envelope, result: TaskResult ) -> bool:
        return False


class AgentWorker:

    def __init__( self,
                  agent:     str,
                  transport: IMessageTransport,
                  executor:  ITaskExecutor,
                  events:    IEventPublisher,
                  retries:   IRetryPolicy,
                  liveness:  ITaskLiveness | None = None ) -> None:
        self._agent     = agent
        self._liveness  = liveness or NoLiveness()
        self._transport = transport
        self._executor  = executor
        self._events    = events
        self._retries   = retries

    async def work_one( self, task_id: str | None = None ) -> Envelope | None:
        """Claim and run one request (a named one, or the next). None when idle."""
        envelope = self._claim( task_id )
        if envelope is None:
            return None
        self._publish( "task.claimed", envelope, attempt=envelope.attempts, performer=self._executor.performer )
        result = await self._execute( envelope )
        return self._settle( envelope, result )

    async def drain( self, limit: int = 20 ) -> list[ Envelope ]:
        """Work until the inbox is empty (or `limit` tasks, so a loop can't spin)."""
        done = []
        for _ in range( limit ):
            settled = await self.work_one()
            if settled is None:
                break
            done.append( settled )
        return done

    def recover( self, older_than_seconds: float ) -> list[ Envelope ]:
        """Settle tasks a dead worker left in `in_process`.

        Idempotent work goes back in the queue; anything else is failed and
        reported, never silently re-run -- it may already have changed files.
        A task whose worker still holds its liveness lock is left alone,
        however old it is: a long build is not a dead one.
        """
        cutoff    = utc_now() - timedelta( seconds=older_than_seconds )
        recovered = []
        for stale in self._transport.box( self._agent, "in_process" ):
            if stale.kind is not MessageKind.REQUEST or ( stale.dequeued_at and stale.dequeued_at > cutoff ):
                continue
            if self._liveness.is_alive( stale.id ):
                continue
            result = TaskResult( ok=False, performer=self._executor.performer,
                                 text="interrupted: the worker stopped before the task finished" )
            recovered.append( self._settle( stale, result ))
        return recovered

    # -- internals -----------------------------------------------------------

    def _claim( self, task_id: str | None ) -> Envelope | None:
        candidates = [ e for e in self._transport.box( self._agent, "new" ) if e.kind is MessageKind.REQUEST ]
        for envelope in candidates:
            if task_id is None or envelope.id == task_id:
                claimed = self._transport.claim( self._agent, envelope.id )
                if claimed is not None:
                    return claimed
        return None

    async def _execute( self, envelope: Envelope ) -> TaskResult:
        try:
            with self._liveness.hold( envelope.id ):
                return await self._executor.execute( envelope )
        except Exception as exc:   # noqa: BLE001 -- a crash is a failed task, not a dead worker
            return TaskResult( ok=False, performer=self._executor.performer,
                               text=f"{ type( exc ).__name__ }: { exc }" )

    def _settle( self, envelope: Envelope, result: TaskResult ) -> Envelope:
        worked = envelope.model_copy( update={ "performer": result.performer })
        if result.ok:
            done = self._transport.complete( self._agent, worked.model_copy( update={ "result": result.text }))
            self._publish( "task.succeeded", done, performer=result.performer, cost_usd=result.cost_usd )
            self._reply( done, TaskStatus.SUCCEEDED, result )
            return done
        if self._retries.should_retry( worked, result ):
            again = self._transport.requeue( self._agent, worked.model_copy( update={ "error": result.text }))
            self._publish( "task.retrying", again, attempt=again.attempts, error=result.text )
            return again
        failed = self._transport.fail( self._agent, worked.model_copy( update={ "error": result.text }))
        self._publish( "task.failed", failed, performer=result.performer, error=result.text,
                       attempts=failed.attempts, **result.details )
        self._reply( failed, TaskStatus.FAILED, result )
        return failed

    def _reply( self, request: Envelope, outcome: TaskStatus, result: TaskResult ) -> None:
        reply = Envelope(
            id             = f"{ request.id }_reply",
            kind           = MessageKind.REPLY,
            sender         = self._agent,
            recipient      = request.sender,
            task_type      = request.task_type,
            text           = result.text,
            priority       = request.priority,
            correlation_id = request.correlation_id,
            parent_id      = request.parent_id,
            trace          = [ *request.trace, self._agent ],
            outcome        = outcome,
            result         = result.text if result.ok else None,
            error          = None if result.ok else result.text,
            performer      = result.performer,
            attempts       = request.attempts,
        )
        self._transport.deliver( reply )
        self._publish( "task.replied", request, outcome=outcome.value )

    def _publish( self, name: str, envelope: Envelope, **details: object ) -> None:
        self._events.publish( TaskEvent( name=name, task_id=envelope.id, agent=self._agent,
                                         details={ k: v for k, v in details.items() if v is not None }))

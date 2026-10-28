"""Executors with no runtime behind them, plus the two structural ones.

* `HelloWorldExecutor` -- the smallest real worker: proves a path end to end.
* `ByTaskType` -- Strategy map: one agent, a different executor per kind of work.
* `ObservedExecutor` -- Decorator: publishes when work starts and ends, with
  the performer and how long it took, without the wrapped executor knowing.
"""
from __future__ import annotations

import time

from ..models import Envelope, TaskEvent, TaskResult
from ..ports import IEventPublisher, ITaskExecutor


class HelloWorldExecutor:

    @property
    def performer( self ) -> str:
        return "python:hello_world"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return TaskResult( ok=True, text="Hello World", performer=self.performer )


class ScriptedExecutor:
    """Answers from a list, then repeats the last answer. A test double that is
    also a legitimate executor for canned replies."""

    def __init__( self, results: list[ TaskResult ], performer: str = "scripted" ) -> None:
        self._results   = list( results )
        self._performer = performer
        self.seen: list[ Envelope ] = []

    @property
    def performer( self ) -> str:
        return self._performer

    async def execute( self, envelope: Envelope ) -> TaskResult:
        self.seen.append( envelope )
        return self._results.pop( 0 ) if len( self._results ) > 1 else self._results[ 0 ]


class ByTaskType:

    def __init__( self, executors: dict[ str, ITaskExecutor ], default: ITaskExecutor | None = None ) -> None:
        self._executors = dict( executors )
        self._default   = default

    @property
    def performer( self ) -> str:
        return "by_task_type(" + ", ".join( f"{ k }={ v.performer }" for k, v in self._executors.items()) + ")"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        executor = self._executors.get( envelope.task_type, self._default )
        if executor is None:
            return TaskResult( ok=False, performer="by_task_type",
                               text=f"no executor is installed for { envelope.task_type } work" )
        return await executor.execute( envelope )


class ObservedExecutor:

    def __init__( self, inner: ITaskExecutor, events: IEventPublisher, agent: str ) -> None:
        self._inner  = inner
        self._events = events
        self._agent  = agent

    @property
    def performer( self ) -> str:
        return self._inner.performer

    async def execute( self, envelope: Envelope ) -> TaskResult:
        self._events.publish( TaskEvent( name="task.executing", task_id=envelope.id, agent=self._agent,
                                         details={ "performer": self._inner.performer,
                                                   "task_type": envelope.task_type }))
        started = time.monotonic()
        result  = await self._inner.execute( envelope )
        self._events.publish( TaskEvent( name="task.executed", task_id=envelope.id, agent=self._agent,
                                         details={ "performer": result.performer, "ok": result.ok,
                                                   "seconds": round( time.monotonic() - started, 3 ),
                                                   **{ k: v for k, v in result.details.items()
                                                       if isinstance( v, ( str, int, float, bool ))}}))
        return result

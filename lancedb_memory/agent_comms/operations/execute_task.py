"""ExecuteTask -- work one agent's inbox: one named task, or everything queued."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..worker import AgentWorker
from ._named import Named
from .base import OperationOutcome


class ExecuteTask( Named ):
    NAME        = "execute_task"
    DESCRIPTION = "Claim queued work for one agent, run it on that agent's executor, settle and reply"

    def __init__( self, worker_for: Callable[[ str ], AgentWorker ]) -> None:
        self._worker_for = worker_for

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        agent  = str( args[ "agent" ])
        worker = self._worker_for( agent )
        if args.get( "recover_after_seconds" ):
            worker.recover( float( args[ "recover_after_seconds" ]))
        if args.get( "task_id" ):
            one  = asyncio.run( worker.work_one( str( args[ "task_id" ])))
            done = [ one ] if one else []
        else:
            done = asyncio.run( worker.drain())
        if not done:
            return OperationOutcome( ok=True, line=f"Nothing queued for { agent }.", data={ "settled": []})
        summary = ", ".join( f"{ e.id } { e.status.value }" for e in done )
        return OperationOutcome( ok=True, line=f"{ agent } settled { len( done ) } task(s): { summary }.",
                                 data={ "settled": [ e.model_dump( mode="json" ) for e in done ]})

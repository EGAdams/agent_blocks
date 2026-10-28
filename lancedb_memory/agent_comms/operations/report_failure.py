"""ReportFailure -- tell the mechanic that something failed, through the post office.

For failures the post office did not see itself -- an ADW run that died, a
receptionist turn that errored. Failures of post-office tasks are forwarded
automatically by FailureForwarder; this is the manual door to the same room.
"""
from __future__ import annotations

from typing import Any

from ..models import TaskEvent
from ..ports import IEventPublisher
from ._named import Named
from .base import OperationOutcome


class ReportFailure( Named ):
    NAME        = "report_failure"
    DESCRIPTION = "Publish a failure so the mechanic is told and diagnoses it"

    def __init__( self, events: IEventPublisher ) -> None:
        self._events = events

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        task_id = str( args.get( "task_id" ) or f"adw-{ args.get( 'adw_id', 'unknown' )}" )
        self._events.publish( TaskEvent( name="task.failed", task_id=task_id, agent=str( args[ "agent" ]),
                                         details={ "error": str( args.get( "error", "" )),
                                                   "adw_id": str( args.get( "adw_id" ) or "" )}))
        return OperationOutcome( ok=True, line=f"Reported the failure of { task_id }; the mechanic has it.",
                                 data={ "reported": task_id })

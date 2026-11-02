"""Liz's workflow state: a small State machine plus per-step checkpoints.

The states say where the city return is, so "what are you doing?" and
"resume" have one answer however many ADWs have run. A transition not in
`ALLOWED` raises -- a workflow that skipped from IDLE to AWAITING_REVIEW is a
bug, not a state. Pausing remembers where to come back to; FAILED remembers
the step that failed, so only that step reruns.

Checkpoints are keyed by step name. A step with a checkpoint is skipped on a
rerun unless the caller forces it; that is what keeps a resumed workflow from
re-downloading forms or re-reading the federal return.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .store import liz_home, write_private


class WorkflowState( str, Enum ):
    IDLE                   = "IDLE"
    VERIFYING_STATUS       = "VERIFYING_STATUS"        # filing status, extension, residency
    ACQUIRING_FORMS        = "ACQUIRING_FORMS"
    COMPARING_FORMS        = "COMPARING_FORMS"
    PUBLISHING_REPORT      = "PUBLISHING_REPORT"
    READING_FEDERAL_RETURN = "READING_FEDERAL_RETURN"
    READING_FINANCE        = "READING_FINANCE"
    AWAITING_DOCUMENT_SCAN = "AWAITING_DOCUMENT_SCAN"
    CALCULATING            = "CALCULATING"
    POPULATING_PDF         = "POPULATING_PDF"
    RECONCILING            = "RECONCILING"
    AWAITING_REVIEW        = "AWAITING_REVIEW"
    COMPLETED              = "COMPLETED"
    PAUSED                 = "PAUSED"
    FAILED                 = "FAILED"


_WORK = { WorkflowState.VERIFYING_STATUS, WorkflowState.ACQUIRING_FORMS, WorkflowState.COMPARING_FORMS,
          WorkflowState.PUBLISHING_REPORT, WorkflowState.READING_FEDERAL_RETURN, WorkflowState.READING_FINANCE,
          WorkflowState.CALCULATING, WorkflowState.POPULATING_PDF, WorkflowState.RECONCILING }

#: Any working state may move to any other working state (steps rerun out of
#: order when one is forced), and every state may pause or fail.
ALLOWED: dict[ WorkflowState, set[ WorkflowState ]] = {
    WorkflowState.IDLE:                   _WORK,
    **{ s: _WORK | { WorkflowState.AWAITING_DOCUMENT_SCAN, WorkflowState.AWAITING_REVIEW } for s in _WORK },
    WorkflowState.AWAITING_DOCUMENT_SCAN: _WORK | { WorkflowState.AWAITING_REVIEW },
    WorkflowState.AWAITING_REVIEW:        _WORK | { WorkflowState.COMPLETED, WorkflowState.AWAITING_DOCUMENT_SCAN },
    WorkflowState.COMPLETED:              _WORK,
    WorkflowState.PAUSED:                 set( WorkflowState ) - { WorkflowState.PAUSED },
    WorkflowState.FAILED:                 _WORK | { WorkflowState.IDLE },
}
for _state in list( ALLOWED ):
    ALLOWED[ _state ] = ALLOWED[ _state ] | { WorkflowState.PAUSED, WorkflowState.FAILED }


class Transition( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    at:     datetime
    before: WorkflowState
    after:  WorkflowState
    why:    str = ""


class Checkpoint( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    step:     str
    at:       datetime
    outputs:  list[ str ] = Field( default_factory=list )
    summary:  str = ""


class LizWorkflow( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    state:       WorkflowState = WorkflowState.IDLE
    resume_to:   WorkflowState | None = None
    failed_step: str = ""
    checkpoints: dict[ str, Checkpoint ] = Field( default_factory=dict )
    history:     list[ Transition ] = Field( default_factory=list )

    def move( self, after: WorkflowState, why: str = "" ) -> None:
        if after == self.state:
            return
        if after not in ALLOWED[ self.state ]:
            raise ValueError( f"Liz can't go from { self.state.value } to { after.value }." )
        if after is WorkflowState.PAUSED:
            self.resume_to = self.state
        self.history.append( Transition( at=datetime.now().astimezone(), before=self.state, after=after, why=why ))
        self.state = after

    def resume( self ) -> WorkflowState:
        """Back to where a pause or a failure left off; IDLE if there is nowhere."""
        target = self.resume_to or WorkflowState.IDLE
        if self.state is WorkflowState.FAILED:
            target = WorkflowState.IDLE
        if self.state in ( WorkflowState.PAUSED, WorkflowState.FAILED ):
            self.history.append( Transition( at=datetime.now().astimezone(), before=self.state, after=target,
                                             why="resume" ))
            self.state, self.resume_to = target, None
        return self.state

    def done( self, step: str ) -> bool:
        return step in self.checkpoints

    def checkpoint( self, step: str, outputs: list[ str ], summary: str = "" ) -> None:
        self.checkpoints[ step ] = Checkpoint( step=step, at=datetime.now().astimezone(), outputs=outputs,
                                               summary=summary )
        if self.failed_step == step:
            self.failed_step = ""

    def fail( self, step: str, why: str ) -> None:
        self.failed_step = step
        self.move( WorkflowState.FAILED, f"{ step }: { why }"[ :300 ])


class JsonWorkflowStore:
    """`<home>/state.json` -- the one ITaxWorkflowState on disk."""

    def __init__( self, home: Path | None = None ) -> None:
        self._path = ( home or liz_home()) / "state.json"

    def load( self ) -> LizWorkflow:
        try:
            return LizWorkflow.model_validate_json( self._path.read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            return LizWorkflow()

    def save( self, flow: LizWorkflow ) -> None:
        write_private( self._path, flow.model_dump_json( indent=2 ) + "\n" )


class InMemoryWorkflowStore:
    def __init__( self ) -> None:
        self.flow = LizWorkflow()

    def load( self ) -> LizWorkflow:
        return self.flow.model_copy( deep=True )

    def save( self, flow: LizWorkflow ) -> None:
        self.flow = flow.model_copy( deep=True )


def describe( flow: LizWorkflow ) -> str:
    done = ", ".join( flow.checkpoints ) or "nothing yet"
    extra = f" (stopped at { flow.failed_step })" if flow.failed_step else ""
    return f"Liz is { flow.state.value.replace( '_', ' ' ).lower()}{ extra }. Steps done: { done }."

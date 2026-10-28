"""IOperation -- one small, independently executable piece of agent communication.

Each operation is a Command: constructed with the interfaces it needs, run
with a dict of arguments, answering an `OperationOutcome` (ok, one spoken
line, structured data). Nothing about an operation knows who calls it -- a
receptionist's runner in-process, or `adws/adw_op_<name>.py` as its own ADW
run -- or which agent implements the interfaces it was given. That is what
lets one operation's implementation be swapped (deterministic routing today,
a model-backed router tomorrow) without the workflow around it changing.

`OperationSequence` is the Composite: a workflow made of operations is itself
an operation, so a composed ADW and a one-step ADW run the same way.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class OperationOutcome( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    ok:   bool
    line: str                                         # the one sentence a caller can repeat
    data: dict[ str, Any ] = Field( default_factory=dict )   # handed to the next operation in a sequence


class IOperation( Protocol ):
    @property
    def name( self ) -> str: ...

    @property
    def description( self ) -> str: ...

    @property
    def performer( self ) -> str:
        """Which implementation does the work -- logged as the phase owner."""
        ...

    def run( self, args: dict[ str, Any ]) -> OperationOutcome: ...


class OperationSequence:
    """IOperation Composite. Each step's data is merged into the next step's
    arguments; the first failure stops the sequence and is its outcome.

    `skip_when` lets a step opt out on the arguments so far (e.g. "await the
    reply" is skipped for an asynchronous send), keeping the sequence data.
    """

    def __init__( self, name: str, description: str, steps: list[ IOperation ],
                  skip_when: dict[ str, str ] | None = None ) -> None:
        self._name        = name
        self._description = description
        self._steps       = list( steps )
        self._skip_when   = dict( skip_when or {})       # step name -> argument that, when truthy, skips it
        self.trail: list[ tuple[ str, OperationOutcome ]] = []

    @property
    def name( self ) -> str:
        return self._name

    @property
    def description( self ) -> str:
        return self._description

    @property
    def performer( self ) -> str:
        return " -> ".join( s.name for s in self._steps )

    @property
    def steps( self ) -> list[ IOperation ]:
        return list( self._steps )

    def run( self, args: dict[ str, Any ],
             step_runner: Callable[[ "IOperation", dict[ str, Any ]], OperationOutcome ] | None = None
             ) -> OperationOutcome:
        """`step_runner` wraps each step's execution -- the ADW harness passes
        one that runs every step as its own lifecycle phase."""
        run_step = step_runner or ( lambda step, step_args: step.run( step_args ))
        self.trail = []
        current = dict( args )
        outcome = OperationOutcome( ok=True, line="nothing to do" )
        for step in self._steps:
            skip = self._skip_when.get( step.name )
            if skip and current.get( skip ):
                continue
            outcome = run_step( step, current )
            self.trail.append(( step.name, outcome ))
            if not outcome.ok:
                return outcome
            current.update( outcome.data )
        return outcome.model_copy( update={ "data": current })

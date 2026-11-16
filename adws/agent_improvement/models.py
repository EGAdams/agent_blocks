"""Typed records for one improvement cycle. Nothing here imports the RSI framework, SSSF or a subprocess."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def now() -> str:
    """UTC, millisecond precision, with the timezone written out."""
    return datetime.now( timezone.utc ).isoformat( timespec="milliseconds" )


class CycleState( str, Enum ):
    RUNNING           = "running"
    NO_DEFICIENCY     = "no_deficiency"          # the baseline passed: nothing to improve, nothing invented
    AWAITING_APPROVAL = "awaiting_approval"      # gates allowed; a named approver has not decided yet
    ACTIVATED         = "activated"
    REJECTED          = "rejected"
    ROLLED_BACK       = "rolled_back"
    CANCELLED         = "cancelled"
    FAILED            = "failed"


TERMINAL = frozenset({ CycleState.NO_DEFICIENCY, CycleState.ACTIVATED, CycleState.REJECTED,
                       CycleState.ROLLED_BACK, CycleState.CANCELLED, CycleState.FAILED })


class TaskRole( str, Enum ):
    TRAINING   = "training"       # the improver sees these results
    REGRESSION = "regression"     # behaviour that must not get worse
    HELD_OUT   = "held_out"       # the improver never sees these


class TaskSpec( BaseModel ):
    """One repeatable thing to ask the target, and what a right answer has to contain."""

    name:       str
    role:       TaskRole
    input_text: str
    expect:     dict[ str, Any ] = Field( default_factory=dict )   # read by the evaluator only


class Budgets( BaseModel ):
    """Limits the cycle is held to. Read by the engine; never reachable from a candidate."""

    max_generations:       int   = 3       # cycles in one lineage
    max_attempts:          int   = 3       # ADW runs of one cycle (resumes included)
    max_concurrent_cycles: int   = 1       # per target
    max_worker_runs:       int   = 14      # turns of the agent under test
    max_estimated_tokens:  int   = 120_000
    cycle_seconds:         float = 3600.0
    worker_seconds:        float = 240.0   # one turn
    pace_seconds:          float = 20.0    # between turns, for a shared rate limit
    approval_wait_seconds: float = 86_400.0


class Evidence( BaseModel ):
    source: str            # where it came from, e.g. "reference:machine_ops/commands.py"
    text:   str


class PromptPatch( BaseModel ):
    """An improver's whole answer: replace `find` with `replace`, once, inside the permitted section."""

    find:             str
    replace:          str
    summary:          str
    expected_benefit: str
    author:           str = ""


class ProposalRecord( BaseModel ):
    proposal_id:        int = 0              # the RSI improvement_proposal row, once saved
    patch:              PromptPatch
    candidate_revision: str = ""
    candidate_sha256:   str = ""
    diff_path:          str = ""
    valid:              bool | None = None
    problems:           list[ str ] = Field( default_factory=list )


class ArmScore( BaseModel ):
    task:   str
    role:   str
    arm:    str
    passed: list[ str ] = Field( default_factory=list )
    failed: list[ str ] = Field( default_factory=list )
    notes:  str = ""


class GateOutcome( BaseModel ):
    name:     str
    decision: str
    reason:   str


class Decision( BaseModel ):
    approved: bool
    approver: str
    reason:   str = ""
    at:       str = Field( default_factory=now )
    key:      str = ""        # idempotency key of the request that carried it


class CycleRecord( BaseModel ):
    """The durable record of one cycle. One JSON file; every phase rewrites it when it finishes."""

    cycle_id:      str
    target:        str
    environment:   str                       # "pilot" or "live"
    state:         CycleState = CycleState.RUNNING
    reason:        str = ""
    parent_cycle:  str = ""
    generation:    int = 1
    attempt:       int = 0
    attempt_errors: list[ str ] = Field( default_factory=list )   # one line per failed attempt; never the decision
    adw_ids:       list[ str ] = Field( default_factory=list )
    machine:       str = ""
    request_key:   str = ""                  # idempotency key of the request that started it
    requested_by:  str = ""
    started_at:    str = Field( default_factory=now )
    updated_at:    str = Field( default_factory=now )
    completed:     list[ str ] = Field( default_factory=list )   # phase keys, in order
    cancel_requested: bool = False

    baseline_revision:  str = ""
    baseline_sha256:    str = ""
    candidate_revision: str = ""
    activated_revision: str = ""
    loaded_sha256:      str = ""             # what the target was seen to load after activation / rollback
    judge_fingerprint:  str = ""             # evaluator code + task catalog + gate list, taken before any proposal
    suite_version:      str = ""
    model:              str = ""
    budgets:            Budgets = Field( default_factory=Budgets )
    worker_runs:        int = 0
    estimated_tokens:   int = 0
    arm_tokens:         dict[ str, int ] = Field( default_factory=dict )   # experiment runs only, per arm
    token_source:       str = ""             # "model" or "prompt_chars/4": the figures are labelled estimates

    evidence:       list[ Evidence ]  = Field( default_factory=list )
    baseline:       list[ ArmScore ]  = Field( default_factory=list )
    proposal:       ProposalRecord | None = None
    experiment_ids: dict[ str, str ]  = Field( default_factory=dict )   # "training" / "held_out" -> RSI experiment id
    scores:         list[ ArmScore ]  = Field( default_factory=list )
    regressions:    list[ str ] = Field( default_factory=list )
    improvements:   list[ str ] = Field( default_factory=list )
    gates:          list[ GateOutcome ] = Field( default_factory=list )
    gate_decision:  str = ""
    decision:       Decision | None = None
    bridge_tasks:   list[ str ] = Field( default_factory=list )
    rollback:       dict[ str, Any ] = Field( default_factory=dict )
    simulated_faults: list[ str ] = Field( default_factory=list )
    report_path:    str = ""

    def done( self, phase: str ) -> bool:
        return phase in self.completed

    def terminal( self ) -> bool:
        return self.state in TERMINAL

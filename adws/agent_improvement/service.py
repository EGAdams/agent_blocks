"""The operations on improvement cycles, callable without any ADW.

The two ADWs (`adw_improvement_cycle.py`, `adw_improvement_ops.py`) are thin shells over this module, and a
test or a plain script can call it directly. Every operation that changes a cycle takes an idempotency key
or is idempotent by its nature:

    open_cycle   a request key already seen returns that cycle; an existing cycle id is resumed, attempt + 1
    decide       the same key twice is one decision; a different, conflicting decision is refused
    cancel       a second cancel answers "already ..."
    rollback     a rolled-back cycle answers with the rollback already recorded

Nothing here names a target: the profile is passed in.
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .engine import Cycle, TargetProfile, record_decision, render_report, request_cancel, rollback
from .interfaces import IImprover
from .models import CycleRecord, CycleState, Decision, TaskRole
from .store import FileCycleStore, RsiStore

ACCEPTED = frozenset({ CycleState.ACTIVATED, CycleState.NO_DEFICIENCY, CycleState.AWAITING_APPROVAL,
                       CycleState.REJECTED, CycleState.ROLLED_BACK })   # honest outcomes; FAILED / CANCELLED are not


class Refused( RuntimeError ):
    """The operation was not allowed; the message says why. Nothing was changed."""


class Workspace:
    """Where one target's cycles live: its RSI database and its cycle records."""

    def __init__( self, profile: TargetProfile, data_root: Path ):
        self.profile = profile
        self.root    = data_root / profile.name.lower()
        self.cycles  = FileCycleStore( self.root )
        self._store: RsiStore | None = None

    @property
    def store( self ) -> RsiStore:
        if self._store is None:
            self._store = RsiStore( self.root / "improvement.sqlite3" )
        return self._store

    def environment( self, name: str ) -> Any:
        if name not in self.profile.environments:
            raise Refused( f"{ self.profile.name } has no { name !r} environment; known: "
                           f"{ ', '.join( sorted( self.profile.environments ))}" )
        return self.profile.environments[ name ]()

    def cycle( self, record: CycleRecord, improver: IImprover | None = None,
               on_activation_write: Callable[[], None] | None = None ) -> Cycle:
        return Cycle( profile=self.profile, record=record, cycles=self.cycles, store=self.store,
                      environment=self.environment( record.environment ), improver=improver or _NoImprover(),
                      on_activation_write=on_activation_write )


class _NoImprover:
    """For operations that never propose (decide, rollback): asking it is a programming error."""

    name = "none"

    def propose( self, *_: Any ) -> Any:
        raise RuntimeError( "this operation does not propose" )


def new_cycle_id( target: str ) -> str:
    return f"{ target.lower()}-{ datetime.now( timezone.utc ).strftime( '%Y%m%dT%H%M%SZ' )}-{ uuid.uuid4().hex[ :6 ]}"


def open_cycle( ws: Workspace, *, environment: str, adw_id: str, cycle_id: str = "", parent_cycle: str = "",
                request_key: str = "", requested_by: str = "", simulated_faults: list[ str ] | None = None
                ) -> tuple[ CycleRecord, str ]:
    """The cycle this run works on, and how it was found: "created", "resumed" or "duplicate"."""
    if request_key:
        seen = ws.cycles.by_request_key( request_key )
        if seen is not None and cycle_id and seen.cycle_id != cycle_id:
            raise Refused( f"request key { request_key !r} already belongs to cycle { seen.cycle_id }" )
        if seen is not None and not cycle_id:
            return seen, "duplicate"
    if cycle_id and ( ws.root / "cycles" / cycle_id / "cycle.json" ).is_file():
        record = ws.cycles.load( cycle_id )
        if ( record.terminal() and record.done( "report" )) or record.state == CycleState.AWAITING_APPROVAL:
            return record, "duplicate"           # finished, or waiting for a person: running it again does nothing
        if record.environment != environment:
            raise Refused( f"cycle { cycle_id } is in the { record.environment } environment, not { environment }" )
        if record.attempt >= record.budgets.max_attempts:
            record.state, record.reason = CycleState.FAILED, f"attempt limit { record.budgets.max_attempts } reached"
            ws.cycles.save( record )
            return record, "duplicate"
        record.attempt += 1
        record.adw_ids.append( adw_id )
        record.simulated_faults += [ f for f in ( simulated_faults or []) if f not in record.simulated_faults ]
        ws.cycles.save( record )
        return record, "resumed"
    generation = 1
    if parent_cycle:
        parent = ws.cycles.load( parent_cycle )
        if not parent.terminal():
            raise Refused( f"the parent cycle { parent_cycle } is still { parent.state.value }" )
        generation = parent.generation + 1
    record = CycleRecord( cycle_id=cycle_id or new_cycle_id( ws.profile.name ), target=ws.profile.name,
                          environment=environment, parent_cycle=parent_cycle, generation=generation, attempt=1,
                          adw_ids=[ adw_id ], machine=socket.gethostname(), request_key=request_key,
                          requested_by=requested_by, budgets=ws.profile.budgets,
                          simulated_faults=list( simulated_faults or []))
    try:
        ws.environment( environment ).read()     # refuses an unknown or unwired environment before anything is written
    except Refused:
        raise
    except Exception as error:                   # noqa: BLE001
        raise Refused( f"the { environment } environment cannot be read: { error }" ) from error
    ws.cycles.save( record )
    return record, "created"


# --------------------------------------------------------------------------- read operations

def status( ws: Workspace, cycle_id: str ) -> dict[ str, Any ]:
    r = ws.cycles.load( cycle_id )
    return { "cycle_id": r.cycle_id, "target": r.target, "environment": r.environment, "state": r.state.value,
             "reason": r.reason, "generation": r.generation, "parent_cycle": r.parent_cycle, "attempt": r.attempt,
             "adw_ids": r.adw_ids, "completed": r.completed, "baseline_revision": r.baseline_revision,
             "candidate_revision": r.candidate_revision, "activated_revision": r.activated_revision,
             "loaded_sha256": r.loaded_sha256[ :16 ], "gate_decision": r.gate_decision,
             "worker_runs": r.worker_runs, "estimated_tokens": r.estimated_tokens, "updated_at": r.updated_at,
             "bridge_tasks": r.bridge_tasks, "simulated_faults": r.simulated_faults, "health": health( ws, cycle_id )}


def proposal( ws: Workspace, cycle_id: str ) -> dict[ str, Any ]:
    r = ws.cycles.load( cycle_id )
    if r.proposal is None:
        return { "cycle_id": cycle_id, "proposal": None, "state": r.state.value }
    diff = Path( r.proposal.diff_path ).read_text( encoding="utf-8" ) if r.proposal.diff_path else ""
    return { "cycle_id": cycle_id, "proposal_id": r.proposal.proposal_id, "valid": r.proposal.valid,
             "problems": r.proposal.problems, "summary": r.proposal.patch.summary,
             "expected_benefit": r.proposal.patch.expected_benefit, "author": r.proposal.patch.author,
             "baseline_revision": r.baseline_revision, "candidate_revision": r.proposal.candidate_revision,
             "diff": diff }


def evaluation( ws: Workspace, cycle_id: str ) -> dict[ str, Any ]:
    r = ws.cycles.load( cycle_id )
    def arm( name: str ) -> list[ dict[ str, Any ]]:
        return [ s.model_dump() for s in r.scores if s.arm == name ]
    return { "cycle_id": cycle_id, "baseline_training": [ s.model_dump() for s in r.baseline ],
             "experiment": { "baseline": arm( "baseline" ), "candidate": arm( "candidate" )},
             "held_out_tasks": [ t.name for t in ws.profile.catalog.tasks() if t.role == TaskRole.HELD_OUT ],
             "improvements": r.improvements, "regressions": r.regressions, "experiment_ids": r.experiment_ids,
             "arm_tokens": r.arm_tokens, "token_source": r.token_source,
             "gates": [ g.model_dump() for g in r.gates ], "gate_decision": r.gate_decision }


def report( ws: Workspace, cycle_id: str ) -> dict[ str, Any ]:
    r = ws.cycles.load( cycle_id )
    text = Path( r.report_path ).read_text( encoding="utf-8" ) if r.report_path else "(no report yet)"
    return { "cycle_id": cycle_id, "report_path": r.report_path, "report": text }


def lineage( ws: Workspace, cycle_id: str = "" ) -> dict[ str, Any ]:
    """Every cycle of the target as a chain of generations; with `cycle_id`, the chain that ends there."""
    everything = { c.cycle_id: c for c in ws.cycles.all()}
    chain: list[ CycleRecord ] = []
    if cycle_id:
        at = everything.get( cycle_id )
        while at is not None:
            chain.insert( 0, at )
            at = everything.get( at.parent_cycle ) if at.parent_cycle else None
    else:
        chain = list( everything.values())
    active = ws.store.revisions.get_active_revision( ws.profile.name )
    return { "target": ws.profile.name, "active_revision": active.wrapper_revision if active else "",
             "max_generations": ws.profile.budgets.max_generations,
             "cycles": [ { "cycle_id": c.cycle_id, "generation": c.generation, "parent": c.parent_cycle,
                           "environment": c.environment, "state": c.state.value, "baseline": c.baseline_revision,
                           "candidate": c.candidate_revision, "activated": c.activated_revision,
                           "restored": c.rollback.get( "restored_revision", "" ), "started_at": c.started_at }
                         for c in chain ],
             "next_generation": ( chain[ -1 ].generation + 1 ) if chain else 1,
             "next_allowed": bool( chain ) and chain[ -1 ].terminal()
                             and chain[ -1 ].generation < ws.profile.budgets.max_generations }


def health( ws: Workspace, cycle_id: str, stalled_after_seconds: float = 900.0 ) -> dict[ str, Any ]:
    """Is anything wrong with this cycle? Waiting for a person is not a stuck worker."""
    r = ws.cycles.load( cycle_id )
    slot = ws.root / "slots" / f"{ cycle_id }.pid"
    alive = False
    if slot.is_file():
        try:
            os.kill( int( slot.read_text( encoding="utf-8" ).strip()), 0 )
            alive = True
        except ( ValueError, ProcessLookupError, OSError ):
            alive = False
    quiet = ( datetime.now( timezone.utc ) - datetime.fromisoformat( r.updated_at )).total_seconds()
    if r.terminal():
        verdict = "finished"
    elif r.state == CycleState.AWAITING_APPROVAL:
        verdict = "waiting_for_person"
    elif not alive:
        verdict = "stalled"                       # RUNNING, but no process holds its slot: it died; resume it
    elif quiet > stalled_after_seconds:
        verdict = "no_progress"                   # a process is there, but nothing has been recorded for a while
    else:
        verdict = "running"
    return { "verdict": verdict, "state": r.state.value, "slot_alive": alive, "seconds_since_update": round( quiet, 1 )}


# --------------------------------------------------------------------------- write operations

def decide( ws: Workspace, cycle_id: str, *, approved: bool, approver: str, key: str, reason: str = "" ) -> dict[ str, Any ]:
    if not key:
        raise Refused( "a decision needs an idempotency key (--key)" )
    record = ws.cycles.load( cycle_id )
    if record.decision is None and record.state != CycleState.AWAITING_APPROVAL:
        raise Refused( f"cycle { cycle_id } is { record.state.value }, not waiting for a decision" )
    try:
        outcome = record_decision( ws.cycle( record ), Decision( approved=approved, approver=approver,
                                                                 reason=reason, key=key ))
    except ( ValueError, PermissionError ) as error:
        raise Refused( str( error )) from error
    return { "cycle_id": cycle_id, "outcome": outcome, "state": ws.cycles.load( cycle_id ).state.value,
             "next": ( f"resume: adw_improvement_cycle.py --cycle-id { cycle_id } --environment { record.environment }"
                       if approved else "rejected; the next run of this cycle settles and reports it" )}


def cancel( ws: Workspace, cycle_id: str, by: str ) -> dict[ str, Any ]:
    return { "cycle_id": cycle_id, "outcome": request_cancel( ws.cycles, cycle_id, by ),
             "state": ws.cycles.load( cycle_id ).state.value }


def roll_back( ws: Workspace, cycle_id: str, *, by: str, reason: str ) -> dict[ str, Any ]:
    record = ws.cycles.load( cycle_id )
    if record.state == CycleState.ROLLED_BACK:
        return { "cycle_id": cycle_id, "outcome": "already rolled back", **record.rollback }
    if record.state != CycleState.ACTIVATED:
        raise Refused( f"cycle { cycle_id } is { record.state.value }; only an activated cycle can be rolled back" )
    env = ws.environment( record.environment )
    allowed, why = ws.profile.promotion.may_approve( by, record.environment )
    if env.requires_person and not allowed:
        raise Refused( f"rollback refused: { why }" )
    if not ws.cycles.acquire( cycle_id, ws.profile.budgets.max_concurrent_cycles ):
        raise Refused( "another cycle of this target is running; try again when it stops" )
    try:
        cycle = ws.cycle( record )
        done = rollback( cycle, f"{ reason } (by { by })" )
        Path( record.report_path or cycle.directory / "report.md" ).write_text( render_report( cycle.record ), encoding="utf-8" )
    finally:
        ws.cycles.release( cycle_id )
    return { "cycle_id": cycle_id, "outcome": "rolled back", **{ k: v for k, v in done.items() if not k.startswith( "_" )}}

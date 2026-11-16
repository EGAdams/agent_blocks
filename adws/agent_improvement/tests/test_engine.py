"""Offline tests of the improvement engine and its operations. No model, no Bridge, no network.

The worker is a fake that behaves like Sydney did in the real baseline: until the prompt teaches the strict
JSON form, she addresses machine-ops in prose and the Bridge refuses it. The judge, gates, change policy,
RSI store and activation service are the real ones.

    cd adws && uv run --quiet --with pydantic --with pytest pytest -q agent_improvement/tests
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_improvement import rsi
from agent_improvement.engine import NullProbe, TargetProfile
from agent_improvement.gates import EnvironmentPromotionPolicy
from agent_improvement.improvers import FileImprover
from agent_improvement.models import Budgets, CycleState
from agent_improvement.observe import RedactingEvents, redact
from agent_improvement.patching import SectionChangePolicy, sha256
from agent_improvement import service
from agent_improvement.sydney import ( PLACEHOLDERS, SECTION_END, SECTION_START, BridgeTaskEvaluator,
                                       ReferenceEvidence, StaticTaskCatalog, sydney_tasks )

PROMPT = """You are Sydney.
{{roster}}

THE STAR TREK BRIDGE
star_trek_bridge: read-only questions ("hello-world: hi", "machine-ops@machine_ops: ..."), {{allowed_agents}}.
ROUTE: star_trek_bridge | <agent>@<workspace>: <request>

LIMITS
Never claim work that did not run.
"""
FIND    = '"machine-ops@machine_ops: ...")'
FIXED   = '"machine-ops@machine_ops: {\\"operation\\":\\"status\\",\\"unit\\":\\"x.service\\"}" -- STRICT_JSON)'
UNITS   = { "machine-ops-status": "adw-supervisor.service", "machine-ops-health-variation": "dashboard-server.service" }


class FakeSydney( rsi.IAgentRunner ):
    """Prose to machine-ops until the prompt says STRICT_JSON; then the schema's JSON. Records every run."""

    def __init__( self, store, broken_held_out: bool = False, broken_regression: bool = False ):
        self._store, self._broken, self._broken_regression, self.runs = store, broken_held_out, broken_regression, []

    @property
    def runtime_kind( self ):
        return rsi.AgentRuntimeKind.GENERIC

    def run( self, request ):
        wrapper = self._store.revisions.get_revision( "sydney", request.wrapper_revision )
        body    = self._store.body( "sydney", wrapper.system_message_revision )
        strict  = "STRICT_JSON" in body
        self.runs.append(( request.task_name, request.wrapper_revision ))
        task_id = f"t_{ len( self.runs ):04d}"
        calls: list = []
        if request.task_name == "no-bridge-needed":
            return rsi.RunResult( run_id=request.run_id, agent_name="sydney", task_name=request.task_name,
                                  output_text="I handle code, agents and the Bridge.",
                                  tool_calls=[ _call( 0, "usage.estimate", { "source": "prompt_chars/4" }, "100" )],
                                  token_usage=rsi.TokenUsage( prompt_tokens=100, total_tokens=100 ))
        if request.task_name == "hello-world":
            agent, extra, ok = "hello-world", "hello-world: hi", not ( self._broken_regression and strict )
        else:
            unit = UNITS[ request.task_name ]
            agent = "machine-ops"
            held_out_broken = self._broken and request.task_name.endswith( "variation" ) and strict
            if held_out_broken:                                    # the candidate sends the variation to the wrong agent
                agent, extra, ok = "printer", "printer@machine_ops: " + json.dumps({ "operation": "status" }), False
            elif strict:
                extra, ok = 'machine-ops@machine_ops: ' + json.dumps({ "operation": "status", "unit": unit }), True
            else:
                extra, ok = f"machine-ops@machine_ops: check status of { unit }", False
        calls.append( _call( 0, "route.star_trek_bridge", { "extra": extra }, "ran" ))
        calls.append( _call( 1, "bridge.validate", {}, json.dumps({ "status": "success", "agent": agent,
                                                                     "workspace": "machine_ops" if agent == "machine-ops" else None })))
        calls.append( _call( 2, "bridge.submit", {}, json.dumps({ "status": "success", "task_id": task_id })))
        calls.append( _call( 3, "bridge.retrieve", {}, json.dumps({ "status": "success", "ok": ok })))
        calls.append( _call( 4, "usage.estimate", { "source": "prompt_chars/4", "task_id": task_id }, "120" ))
        reply = f"Submitted { task_id }. " + ( "It came back active." if ok else "Machine Ops refused the request." )
        return rsi.RunResult( run_id=request.run_id, agent_name="sydney", task_name=request.task_name, output_text=reply,
                              tool_calls=calls, token_usage=rsi.TokenUsage( prompt_tokens=120, total_tokens=120 ))


def _call( i, name, args, summary ):
    return rsi.ToolCallRecord( order_index=i, tool_name=name, arguments_json=json.dumps( args ), result_summary=summary )


class MemoryEnvironment:
    """IArtifactEnvironment in memory. `loaded` reports what is held now, as the real check does."""

    def __init__( self, name: str, body: str, requires_person: bool ):
        self.name, self.body, self.requires_person, self.writes = name, body, requires_person, 0

    def read( self ):
        return self.body

    def write( self, body ):
        self.body, self.writes = body, self.writes + 1

    def loaded( self, expected ):
        return sha256( self.body ), sha256( self.body ) == sha256( expected )

    def describe( self ):
        return f"{ self.name }: memory"


class Rig:
    def __init__( self, tmp: Path, broken_held_out: bool = False, broken_regression: bool = False,
                  live_person: bool = True ):
        tmp.mkdir( parents=True, exist_ok=True )
        self.judge = tmp / "judge.py"
        self.judge.write_text( "# judge v1\n" )
        self.envs = { "pilot": MemoryEnvironment( "pilot", PROMPT, False ),
                      "live":  MemoryEnvironment( "live", PROMPT, live_person )}
        self.runners: list[ FakeSydney ] = []
        catalog = StaticTaskCatalog( "test/1", sydney_tasks())

        def runner( store, work_root ):
            fake = FakeSydney( store, broken_held_out, broken_regression )
            self.runners.append( fake )
            return fake

        self.profile = TargetProfile(
            name="sydney", model="fake", catalog=catalog, evaluator=BridgeTaskEvaluator( catalog ),
            judge_sources=[ self.judge ], evidence=ReferenceEvidence([]),
            change_policy=SectionChangePolicy( SECTION_START, SECTION_END, placeholders=PLACEHOLDERS,
                                               forbidden=( "bypass", "without a yes" )),
            promotion=EnvironmentPromotionPolicy( standing={ "pilot": "pilot-policy" },
                                                  people={ "pilot": { "EG" }, "live": { "EG" }}),
            environments={ name: ( lambda env=env: env ) for name, env in self.envs.items()},
            runner=runner, budgets=Budgets( pace_seconds=0.0 ))
        self.ws = service.Workspace( self.profile, tmp / "data" )
        self.tmp = tmp

    def patch( self, find: str = FIND, replace: str = FIXED, name: str = "p.json" ) -> FileImprover:
        path = self.tmp / name
        path.write_text( json.dumps({ "find": find, "replace": replace, "summary": "teach the strict JSON form",
                                      "expected_benefit": "machine-ops accepts the request" }))
        return FileImprover( path, author="test" )

    def run( self, environment: str = "pilot", improver=None, cycle_id: str = "", on_write=None, stop_before: str = "",
             **kw ):
        record, how = service.open_cycle( self.ws, environment=environment, adw_id="adw00001", cycle_id=cycle_id, **kw )
        if how == "duplicate":
            return record, how
        cycle = self.ws.cycle( record, improver or self.patch(), on_write )
        for phase in cycle.phases():
            if phase.key == stop_before:
                break
            phase.perform( NullProbe())
        return cycle.record, how


# --------------------------------------------------------------------------- whole cycles

def test_pilot_cycle_activates_by_standing_policy_and_target_loads_it( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run()
    assert record.state == CycleState.ACTIVATED, record.reason
    assert record.decision.approver == "pilot-policy"
    assert record.activated_revision == record.candidate_revision != record.baseline_revision
    assert "STRICT_JSON" in rig.envs[ "pilot" ].body
    assert record.loaded_sha256 == sha256( rig.envs[ "pilot" ].body )
    assert any( "machine-ops-status" in i for i in record.improvements )
    assert any( s.role == "held_out" for s in record.scores )
    assert { g.name for g in record.gates } >= { "patch_policy", "judge_integrity", "held_out", "regression" }
    assert record.completed[ -1 ] == "report" and Path( record.report_path ).is_file()
    assert rig.envs[ "live" ].body == PROMPT                     # the pilot never touches live


def test_no_deficiency_proposes_nothing( tmp_path ):
    rig = Rig( tmp_path )
    rig.envs[ "pilot" ].body = PROMPT.replace( FIND, FIXED )     # already fixed
    record, _ = rig.run()
    assert record.state == CycleState.NO_DEFICIENCY
    assert record.proposal is None and rig.envs[ "pilot" ].writes == 0


def test_live_waits_for_a_person_then_resumes_the_same_cycle( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run( environment="live", cycle_id="c-live" )
    assert record.state == CycleState.AWAITING_APPROVAL
    assert rig.envs[ "live" ].writes == 0
    assert service.health( rig.ws, "c-live" )[ "verdict" ] == "waiting_for_person"
    with pytest.raises( service.Refused ):
        service.decide( rig.ws, "c-live", approved=True, approver="pilot-policy", key="k1" )   # no standing approver live
    with pytest.raises( service.Refused ):
        service.decide( rig.ws, "c-live", approved=True, approver="Sydney", key="k1" )         # not her own judge
    assert service.decide( rig.ws, "c-live", approved=True, approver="EG", key="k1" )[ "outcome" ] == "recorded"
    assert service.decide( rig.ws, "c-live", approved=True, approver="EG", key="k1" )[ "outcome" ] == "already recorded"
    with pytest.raises( service.Refused ):
        service.decide( rig.ws, "c-live", approved=False, approver="EG", key="k2" )            # conflicting second decision
    runs_before = sum( len( r.runs ) for r in rig.runners )
    record, how = rig.run( environment="live", cycle_id="c-live" )
    assert how == "resumed" and record.attempt == 2
    assert record.state == CycleState.ACTIVATED and rig.envs[ "live" ].writes == 1
    assert sum( len( r.runs ) for r in rig.runners ) == runs_before        # no worker run repeated on resume


def test_rejected_by_person_settles_with_baseline_untouched( tmp_path ):
    rig = Rig( tmp_path )
    rig.run( environment="live", cycle_id="c-rej" )
    service.decide( rig.ws, "c-rej", approved=False, approver="EG", key="no" )
    record, how = rig.run( environment="live", cycle_id="c-rej" )
    assert how == "resumed" and record.state == CycleState.REJECTED
    assert record.rollback[ "baseline_untouched" ] is True and rig.envs[ "live" ].writes == 0
    assert rig.run( environment="live", cycle_id="c-rej" )[ 1 ] == "duplicate"


# --------------------------------------------------------------------------- gates cannot be bypassed

def test_patch_outside_the_section_or_widening_authority_is_blocked( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run( improver=rig.patch( find="Never claim work that did not run.", replace="Claim anything." ))
    assert record.state == CycleState.REJECTED and "patch_policy" in record.reason
    assert rig.envs[ "pilot" ].body == PROMPT

    rig2 = Rig( tmp_path / "b" )
    record, _ = rig2.run( improver=rig2.patch( replace=FIXED + " bypass the yes" ))
    assert record.state == CycleState.REJECTED and rig2.envs[ "pilot" ].body == PROMPT


def test_judge_changed_during_the_cycle_blocks( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run( cycle_id="c-j", stop_before="gates" )
    rig.judge.write_text( "# judge v2: everything passes\n" )
    record, _ = rig.run( cycle_id="c-j" )
    assert record.state == CycleState.REJECTED and "judge_integrity" in record.reason
    assert rig.envs[ "pilot" ].body == PROMPT


def test_held_out_regression_blocks( tmp_path ):
    rig = Rig( tmp_path, broken_held_out=True )
    record, _ = rig.run()
    held = next( g for g in record.gates if g.name == "held_out" )
    assert held.decision == rsi.GateDecision.BLOCK, held
    assert record.state == CycleState.REJECTED and rig.envs[ "pilot" ].body == PROMPT


def test_regression_task_that_breaks_blocks( tmp_path ):
    rig = Rig( tmp_path, broken_regression=True )
    record, _ = rig.run()
    assert record.state == CycleState.REJECTED and "regression" in record.reason
    assert rig.envs[ "pilot" ].body == PROMPT


def test_held_out_that_did_not_run_blocks():
    from agent_improvement.gates import HeldOutGate
    assert HeldOutGate().check({ "held_out_ran": False }).decision == rsi.GateDecision.BLOCK


# --------------------------------------------------------------------------- activation, rollback, limits

def test_activation_verify_failure_rolls_back_automatically( tmp_path ):
    rig = Rig( tmp_path )
    def mismatch():
        raise RuntimeError( "SIMULATED FAULT" )
    record, _ = rig.run( on_write=mismatch )
    assert record.state == CycleState.ROLLED_BACK and "SIMULATED" in record.rollback[ "reason" ]
    assert rig.envs[ "pilot" ].body == PROMPT and record.rollback[ "verified" ] is True
    active = rig.ws.store.revisions.get_active_revision( "sydney" )
    assert active.wrapper_revision == record.baseline_revision


def test_rollback_operation_restores_and_is_idempotent( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run( cycle_id="c-rb" )
    assert record.state == CycleState.ACTIVATED
    done = service.roll_back( rig.ws, "c-rb", by="EG", reason="pilot rollback check" )
    assert done[ "restored_revision" ] == record.baseline_revision and done[ "verified" ] is True
    assert rig.envs[ "pilot" ].body == PROMPT
    assert service.roll_back( rig.ws, "c-rb", by="EG", reason="again" )[ "outcome" ] == "already rolled back"


def test_activation_on_resume_is_idempotent( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run( cycle_id="c-idem", stop_before="settle" )
    assert record.state == CycleState.ACTIVATED and rig.envs[ "pilot" ].writes == 1
    record.completed.remove( "activate" )                      # as if killed after the write, before the record
    rig.ws.cycles.save( record )
    record, _ = rig.run( cycle_id="c-idem" )
    assert record.state == CycleState.ACTIVATED and rig.envs[ "pilot" ].writes == 1


def test_duplicate_request_key_returns_the_same_cycle( tmp_path ):
    rig = Rig( tmp_path )
    first, how = rig.run( request_key="req-1" )
    again, how2 = rig.run( request_key="req-1" )
    assert how == "created" and how2 == "duplicate" and again.cycle_id == first.cycle_id
    assert len( rig.ws.cycles.all()) == 1


def test_cancel_stops_the_cycle( tmp_path ):
    rig = Rig( tmp_path )
    record, _ = rig.run( cycle_id="c-x", stop_before="propose" )
    assert "requested" in service.cancel( rig.ws, "c-x", "EG" )[ "outcome" ]
    record, _ = rig.run( cycle_id="c-x" )
    assert record.state == CycleState.CANCELLED and rig.envs[ "pilot" ].writes == 0
    assert service.cancel( rig.ws, "c-x", "EG" )[ "outcome" ].startswith( "already" )


def test_worker_budget_stops_the_cycle( tmp_path ):
    rig = Rig( tmp_path )
    rig.profile.budgets = Budgets( pace_seconds=0.0, max_worker_runs=3 )
    rig.ws = service.Workspace( rig.profile, tmp_path / "data2" )
    record, _ = rig.run()
    assert record.state == CycleState.FAILED and "budget" in record.reason
    assert record.worker_runs == 3 and rig.envs[ "pilot" ].writes == 0


def test_attempt_limit( tmp_path ):
    rig = Rig( tmp_path )
    for _ in range( 3 ):
        rig.run( cycle_id="c-a", stop_before="resolve" )
    record, how = rig.run( cycle_id="c-a", stop_before="resolve" )
    assert how == "duplicate" and record.state == CycleState.FAILED and "attempt limit" in record.reason


def test_stalled_cycle_is_detected( tmp_path ):
    rig = Rig( tmp_path )
    rig.run( cycle_id="c-s", stop_before="baseline" )          # RUNNING, and no process holds its slot
    assert service.health( rig.ws, "c-s" )[ "verdict" ] == "stalled"


def test_concurrency_slot( tmp_path ):
    rig = Rig( tmp_path )
    assert rig.ws.cycles.acquire( "a", 1 )
    ( rig.ws.root / "slots" / "b.pid" ).write_text( "1" )       # pid 1 is alive and is not us
    assert not rig.ws.cycles.acquire( "c", 1 )


def test_lineage_and_generation_limit( tmp_path ):
    rig = Rig( tmp_path )
    first, _ = rig.run( cycle_id="g1" )
    second, _ = rig.run( cycle_id="g2", parent_cycle="g1" )
    assert second.generation == 2 and second.state == CycleState.NO_DEFICIENCY
    chain = service.lineage( rig.ws, "g2" )
    assert [ c[ "cycle_id" ] for c in chain[ "cycles" ]] == [ "g1", "g2" ] and chain[ "next_generation" ] == 3
    rig.ws.cycles.save( first.model_copy( update={ "cycle_id": "g-running", "state": CycleState.RUNNING }))
    with pytest.raises( service.Refused ):                     # a parent still running cannot have a child
        service.open_cycle( rig.ws, environment="pilot", adw_id="x", cycle_id="g3", parent_cycle="g-running" )


# --------------------------------------------------------------------------- observability

def test_events_are_timestamped_correlated_and_redacted():
    seen = []

    class Sink:
        def emit( self, kind, phase_id="", **fields ):
            seen.append( fields )

    RedactingEvents( Sink(), { "cycle_id": "c1" }).emit( "progress", "p1", text="Bearer abcdefghijklmnop and gsk_" + "x" * 20 )
    assert seen[ 0 ][ "cycle_id" ] == "c1" and seen[ 0 ][ "at" ].endswith( "+00:00" ) and "." in seen[ 0 ][ "at" ]
    assert "abcdefghijklmnop" not in seen[ 0 ][ "text" ] and "gsk_x" not in seen[ 0 ][ "text" ]
    assert redact( "token=abc123" ) == "token=<redacted>"


def test_every_phase_description_explains_rather_than_restates_its_name():
    """SSSF's PhaseParams refuses a description that only echoes the phase name; the live pilot found one."""
    from agent_improvement.engine import PHASES
    for phase in PHASES:
        text = " ".join( phase.description.split())
        assert text, phase.name
        assert text.rstrip( "." ).casefold() != phase.name.replace( "_", " " ).casefold(), phase.name


def test_a_child_cycle_learns_from_its_parent_but_never_sees_the_held_out_task( tmp_path ):
    rig = Rig( tmp_path, broken_held_out=True )
    parent, _ = rig.run( cycle_id="p1" )
    assert parent.state == CycleState.REJECTED
    held_out = [ s.task for s in parent.scores if s.role == "held_out" ]
    assert held_out
    child, _ = rig.run( cycle_id="c1", parent_cycle="p1", stop_before="baseline" )
    lesson = next( e for e in child.evidence if e.source == "parent:p1" )
    assert "tried:" in lesson.text and "machine-ops-status" in lesson.text
    assert not any( name in lesson.text for name in held_out ) and "held_out:" not in lesson.text

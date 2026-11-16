"""The improvement engine: ten phases over one cycle, and the operations a person runs on a cycle.

Callable without any ADW: `Cycle( ... ).phases()` gives ten objects shaped like the debug pilot's
`IPilotStep` (key, name, purpose, description, perform( probe ), dispose()), so the ADW wraps each one in
`MonitoredPhase`, and a test or a plain script calls `perform( probe )` on them in order.

Every phase is idempotent by key: a key already in `cycle.completed` is skipped, so a killed or
resumed run continues the same recorded cycle and never repeats a worker run or a write.

Nothing here names a target. Everything that differs is in the `TargetProfile`.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from . import rsi
from .gates import fingerprint, gate_chain
from .interfaces import IArtifactEnvironment, IImprover
from .models import ( ArmScore, Budgets, CycleRecord, CycleState, Decision, Evidence, GateOutcome,
                      ProposalRecord, TaskRole, TaskSpec )
from .patching import sha256
from .store import FileCycleStore, RsiStore


class CycleCancelled( RuntimeError ):
    """A person asked for the cycle to stop."""


class BudgetExhausted( RuntimeError ):
    """A limit in `Budgets` was reached."""


class TargetProfile( BaseModel ):
    """Everything about one target the engine needs, and nothing it may not change."""

    model_config = ConfigDict( arbitrary_types_allowed=True )

    name:            str                                   # the agent name in the RSI store
    model:           str                                   # the worker's model, recorded on every trace
    catalog:         Any                                   # ITaskCatalog
    evaluator:       Any                                   # rsi.IEvaluator: the judge
    judge_sources:   list[ Path ]                          # fingerprinted: the judge's code
    # Interfaces are Protocols, which pydantic cannot isinstance-check; the comments name the interface.
    evidence:        Any                                   # IEvidenceCollector: the trainer
    change_policy:   Any                                   # IChangePolicy
    promotion:       Any                                   # IPromotionPolicy
    environments:    dict[ str, Any ]                      # name -> Callable[[], IArtifactEnvironment]
    runner:          Callable[[ RsiStore, Path ], Any ]    # ( store, work_root ) -> rsi.IAgentRunner: the worker
    risk_level:      str = "medium"
    required_tests:  list[ str ] = []
    budgets:         Budgets = Budgets()

    def judge_fingerprint( self ) -> str:
        return fingerprint( self.judge_sources,
                            json.dumps([ t.model_dump( mode="json" ) for t in self.catalog.tasks()], sort_keys=True ))


class NullProbe:
    """IPhaseProbe that drops everything: for calling the engine without a debugger."""

    def progress( self, text: str ) -> None: ...
    def waiting( self, reason: str, deadline_seconds: float ) -> None: ...
    def tool_call( self, tool: str, arguments: dict[ str, Any ]) -> str: return ""
    def tool_result( self, call_id: str, tool: str, result: str ) -> None: ...
    def message( self, to_key: str, text: str ) -> None: ...


class SystemClock:
    def monotonic( self ) -> float:
        return time.monotonic()

    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )


def revision_names( body: str ) -> tuple[ str, str ]:
    """( wrapper revision, system-message revision ) named by content, so the same text is one revision."""
    digest = sha256( body )[ :12 ]
    return f"w-{ digest }", f"sm-{ digest }"


# --------------------------------------------------------------------------- the context one run shares

class Cycle:
    """One run over one cycle record. Owns the record while the run holds the cycle's slot."""

    def __init__( self, *, profile: TargetProfile, record: CycleRecord, cycles: FileCycleStore, store: RsiStore,
                  environment: IArtifactEnvironment, improver: IImprover, clock: Any = None,
                  on_activation_write: Callable[[], None] | None = None ):
        self.profile, self.record, self.cycles, self.store = profile, record, cycles, store
        self.environment, self.improver = environment, improver
        self.clock = clock or SystemClock()
        self.on_activation_write = on_activation_write or ( lambda: None )   # test-only fault hook
        self.directory = cycles.directory( record.cycle_id )
        self.probe: Any = NullProbe()
        self._last_run: float | None = None

    # -- record keeping ---------------------------------------------------------
    def save( self ) -> None:
        self.cycles.save( self.record )

    def cancel_requested( self ) -> bool:
        return ( self.directory / "cancel.requested" ).exists()

    def check_limits( self ) -> None:
        if self.cancel_requested():
            raise CycleCancelled( "cancel requested" )
        started = datetime.fromisoformat( self.record.started_at )
        age = ( datetime.now( started.tzinfo ) - started ).total_seconds()
        waited = self.record.rollback.get( "_approval_wait_seconds", 0.0 )
        if age - waited > self.profile.budgets.cycle_seconds:
            raise BudgetExhausted( f"the cycle has run { age:.0f}s; the limit is { self.profile.budgets.cycle_seconds:g}s" )

    def tasks( self, *roles: TaskRole ) -> list[ TaskSpec ]:
        return [ t for t in self.profile.catalog.tasks() if t.role in roles ]

    def runner( self ) -> "BudgetedRunner":
        return BudgetedRunner( self.profile.runner( self.store, self.directory / "work" ), self )

    def phases( self ) -> list[ Any ]:
        return [ cls( self ) for cls in PHASES ]


class BudgetedRunner( rsi.IAgentRunner ):
    """Decorator over the worker: limits, pacing, cancel, correlation and observability. Holds no rules of its own."""

    def __init__( self, inner: Any, cycle: Cycle ):
        self._inner, self._cycle = inner, cycle

    @property
    def runtime_kind( self ) -> rsi.AgentRuntimeKind:
        return self._inner.runtime_kind

    def run( self, request: rsi.RunRequest ) -> rsi.RunResult:
        cycle, record, budgets = self._cycle, self._cycle.record, self._cycle.profile.budgets
        cycle.check_limits()
        if record.worker_runs >= budgets.max_worker_runs:
            raise BudgetExhausted( f"{ record.worker_runs } worker runs; the limit is { budgets.max_worker_runs }" )
        if record.estimated_tokens >= budgets.max_estimated_tokens:
            raise BudgetExhausted( f"{ record.estimated_tokens } estimated tokens; the limit is { budgets.max_estimated_tokens }" )
        if cycle._last_run is not None:
            pause = budgets.pace_seconds - ( cycle.clock.monotonic() - cycle._last_run )
            if pause > 0:
                cycle.probe.progress( f"pacing { pause:.0f}s for the shared model rate limit" )
                cycle.clock.sleep( pause )
        call = cycle.probe.tool_call( "worker.run", { "run_id": request.run_id, "task": request.task_name,
                                                      "revision": request.wrapper_revision, "arm": request.arm.value })
        result = self._inner.run( request )
        cycle._last_run = cycle.clock.monotonic()
        record.worker_runs += 1
        record.estimated_tokens += int( result.token_usage.total_tokens )
        usage = next(( c for c in result.tool_calls if c.tool_name == "usage.estimate" ), None )
        task_id = json.loads( usage.arguments_json ).get( "task_id", "" ) if usage else ""
        if task_id:
            record.bridge_tasks.append( f"{ request.run_id }={ task_id }" )
        cycle.probe.tool_result( call, "worker.run", ( result.error or result.output_text )[ :300 ]
                                 + ( f" [bridge { task_id }]" if task_id else "" ))
        cycle.save()
        return result


# --------------------------------------------------------------------------- the ten phases

class _Phase:
    key = name = purpose = description = ""
    runs_in: frozenset[ CycleState ] = frozenset({ CycleState.RUNNING })

    def __init__( self, cycle: Cycle ):
        self.cycle = cycle

    def perform( self, probe: Any ) -> None:
        cycle, record = self.cycle, self.cycle.record
        cycle.probe = probe
        if record.done( self.key ):
            probe.progress( f"already done in an earlier attempt; skipped" )
            return
        if record.state not in self.runs_in:
            probe.progress( f"not needed: the cycle is { record.state.value }" )
            return
        try:
            cycle.check_limits()
            self.work( probe )
        except CycleCancelled as stop:
            _stop( cycle, CycleState.CANCELLED, str( stop ))
            return
        except BudgetExhausted as stop:
            _stop( cycle, CycleState.FAILED, f"budget: { stop }" )
            return
        record.completed.append( self.key )
        cycle.save()

    def work( self, probe: Any ) -> None:
        raise NotImplementedError

    def dispose( self ) -> None:
        return None


def _stop( cycle: Cycle, state: CycleState, reason: str ) -> None:
    cycle.record.state, cycle.record.reason = state, reason
    cycle.probe.progress( f"cycle { state.value }: { reason }" )
    cycle.save()


class ResolvePhase( _Phase ):
    key, name = "resolve", "resolve_target"
    purpose = "Find the target's improvable artifact, record it as the baseline revision, and fingerprint the judge."
    description = "Resolve target, snapshot the artifact, fingerprint the judge"

    def work( self, probe: Any ) -> None:
        cycle, record, store, agent = self.cycle, self.cycle.record, self.cycle.store, self.cycle.profile.name
        budgets = cycle.profile.budgets
        if record.generation > budgets.max_generations:
            raise BudgetExhausted( f"generation { record.generation }; the lineage limit is { budgets.max_generations }" )
        body = cycle.environment.read()
        wrapper, artifact = revision_names( body )
        if not store.has_revision( agent, wrapper ):
            active = store.revisions.get_active_revision( agent )
            store.artifacts.save_artifact( rsi.PromptArtifact(
                agent_name=agent, artifact_type=rsi.ArtifactType.SYSTEM_MESSAGE, revision=artifact,
                parent_revision=active.system_message_revision if active else None, body=body,
                rationale="observed in the environment at the start of a cycle", author="engine:resolve" ))
            store.revisions.save_revision( rsi.WrapperRevision(
                agent_name=agent, wrapper_revision=wrapper, system_message_revision=artifact,
                toolset_revision="unchanged", prompt_template_revision="unchanged",
                context_strategy_revision="unchanged", parent_wrapper_revision=active.wrapper_revision if active else None,
                rationale="observed in the environment", author="engine:resolve" ))
        active = store.revisions.get_active_revision( agent )
        if active is None or active.wrapper_revision != wrapper:
            probe.progress( f"active revision was { active.wrapper_revision if active else 'none' }; "
                            f"the environment holds { wrapper }, which is now recorded as active" )
            store.revisions.set_active_revision( agent, wrapper )
        record.baseline_revision, record.baseline_sha256 = wrapper, sha256( body )
        record.judge_fingerprint = cycle.profile.judge_fingerprint()
        record.suite_version, record.model = cycle.profile.catalog.version, cycle.profile.model
        probe.progress( f"baseline { wrapper } from { cycle.environment.describe()}; judge { record.judge_fingerprint[ :12 ]}" )


class EvidencePhase( _Phase ):
    key, name = "evidence", "gather_evidence"
    purpose = "Collect what is known before anything is proposed: reference material the trainer names."
    description = "Gather evidence for the improver"

    def work( self, probe: Any ) -> None:
        record = self.cycle.record
        found  = self.cycle.profile.evidence.collect( record )
        if record.parent_cycle:
            found += parent_evidence( self.cycle.cycles.load( record.parent_cycle ))
        record.evidence = found
        probe.progress( f"{ len( found )} evidence items: " + ", ".join( e.source for e in found ))


def parent_evidence( parent: CycleRecord ) -> list[ Evidence ]:
    """What the parent cycle learned, as the next improver may see it: the patch it tried, the gates' verdict and
    how the candidate did on the TRAINING tasks. Held-out tasks and the held-out gate are left out, so a held-out
    variation stays a variation the improver has never been shown."""
    if parent.proposal is None:
        return []
    patch = parent.proposal.patch
    gates = [ f"{ g.name }: { g.decision } -- { g.reason }" for g in parent.gates if g.name != "held_out" ]
    tried = [ f"{ s.task }: failed { ', '.join( s.failed ) or 'nothing' }; reply: { s.notes[ :400 ]}"
              for s in parent.scores if s.arm == "candidate" and s.role == "training" ]
    text  = "\n".join([ f"cycle { parent.cycle_id } ({ parent.state.value }) tried: { patch.summary }",
                         f"its replacement text: { patch.replace[ :600 ]}", *gates, *tried ])
    return [ Evidence( source=f"parent:{ parent.cycle_id }", text=text[ :2000 ])]


class BaselinePhase( _Phase ):
    key, name = "baseline", "evaluate_baseline"
    purpose = "Run the training tasks on the baseline revision and let the judge score them."
    description = "Evaluate the baseline on the training tasks"

    def work( self, probe: Any ) -> None:
        cycle, record = self.cycle, self.cycle.record
        runner = cycle.runner()
        scores: list[ ArmScore ] = []
        for task in cycle.tasks( TaskRole.TRAINING ):
            request = rsi.RunRequest( run_id=f"{ record.cycle_id }:baseline:{ task.name }", agent_name=cycle.profile.name,
                                      task_name=task.name, input_text=task.input_text,
                                      wrapper_revision=record.baseline_revision, metadata={ "llm_name": record.model })
            result = runner.run( request )
            card = _score( cycle, request, result )
            scores.append( ArmScore( task=task.name, role=task.role.value, arm="baseline", passed=card.passed_checks,
                                     failed=card.failed_checks + ([ "transport" ] if not result.succeeded_transport else []),
                                     notes=( result.error or result.output_text )[ :600 ]))
        record.baseline = scores
        record.evidence += [ Evidence( source=f"baseline:{ s.task }", text=f"failed { ', '.join( s.failed )}; reply: { s.notes[ :400 ]}" )
                             for s in scores if s.failed ]
        failing = [ s for s in scores if s.failed ]
        probe.progress( "; ".join( f"{ s.task }: { len( s.passed )} passed, failed { s.failed or 'nothing' }" for s in scores ))
        if not failing:
            _stop( cycle, CycleState.NO_DEFICIENCY, "the baseline passed every training check; nothing to improve" )
        elif all( s.failed == [ "transport" ] for s in failing ):
            _stop( cycle, CycleState.FAILED, "the worker could not be reached; a transport failure is not a deficiency" )


def _score( cycle: Cycle, request: rsi.RunRequest, result: rsi.RunResult ) -> rsi.ScoreCard:
    trace = rsi.TraceRecord( project_name="agent_improvement", agent_name=request.agent_name, llm_name=cycle.record.model,
                             task_name=request.task_name, system_message_revision=request.wrapper_revision,
                             toolset_revision="unchanged", prompt_template_revision="unchanged",
                             context_strategy_revision="unchanged", input_text=request.input_text,
                             agent_output=result.output_text, tool_calls=result.tool_calls, token_usage=result.token_usage )
    trace.id = cycle.store.traces.save_trace( trace )
    return cycle.profile.evaluator.evaluate( trace )


class ProposePhase( _Phase ):
    key, name = "propose", "propose_improvement"
    purpose = "Ask the improver for one small patch, showing it only the permitted section, training failures and evidence."
    description = "Propose an improvement"

    def work( self, probe: Any ) -> None:
        cycle, record = self.cycle, self.cycle.record
        body = cycle.store.body( cycle.profile.name, _sm( cycle, record.baseline_revision ))
        failures = [ s for s in record.baseline if s.failed ]
        call  = probe.tool_call( f"improver.{ cycle.improver.name }", { "failures": [ f.task for f in failures ]})
        patch = cycle.improver.propose( body, cycle.profile.change_policy.permitted_section( body ), failures, record.evidence )
        probe.tool_result( call, f"improver.{ cycle.improver.name }", patch.summary[ :300 ])
        record.proposal = ProposalRecord( patch=patch )


def _sm( cycle: Cycle, wrapper: str ) -> str:
    return cycle.store.revisions.get_revision( cycle.profile.name, wrapper ).system_message_revision


class PreparePhase( _Phase ):
    key, name = "prepare", "prepare_candidate"
    purpose = "Apply the patch under the change policy, record the candidate revision inactive, and write its diff."
    description = "Prepare the candidate revision"

    def work( self, probe: Any ) -> None:
        cycle, record, store, agent = self.cycle, self.cycle.record, self.cycle.store, self.cycle.profile.name
        proposal = record.proposal
        body = store.body( agent, _sm( cycle, record.baseline_revision ))
        candidate, problems = cycle.profile.change_policy.apply( body, proposal.patch )
        proposal.valid, proposal.problems = not problems, problems
        diff = "".join( difflib.unified_diff( body.splitlines( True ), candidate.splitlines( True ),
                                              f"{ record.baseline_revision }/system_prompt.md",
                                              f"candidate/system_prompt.md" ))
        ( cycle.directory / "candidate.diff" ).write_text( diff or "(no change)\n", encoding="utf-8" )
        proposal.diff_path = str( cycle.directory / "candidate.diff" )
        wrapper = ""
        if not problems:
            wrapper, artifact = revision_names( candidate )
            if not store.has_revision( agent, wrapper ):
                store.artifacts.save_artifact( rsi.PromptArtifact(
                    agent_name=agent, artifact_type=rsi.ArtifactType.SYSTEM_MESSAGE, revision=artifact,
                    parent_revision=_sm( cycle, record.baseline_revision ), body=candidate,
                    rationale=proposal.patch.summary, author=proposal.patch.author or cycle.improver.name ))
                store.revisions.save_revision( rsi.WrapperRevision(
                    agent_name=agent, wrapper_revision=wrapper, system_message_revision=artifact,
                    toolset_revision="unchanged", prompt_template_revision="unchanged",
                    context_strategy_revision="unchanged", parent_wrapper_revision=record.baseline_revision,
                    rationale=proposal.patch.summary, author=proposal.patch.author or cycle.improver.name ))
            proposal.candidate_revision, proposal.candidate_sha256 = wrapper, sha256( candidate )
            record.candidate_revision = wrapper
        saved = rsi.ImprovementProposal(
            agent_name=agent, target_artifact_type=rsi.ArtifactType.SYSTEM_MESSAGE,
            target_artifact_revision=_sm( cycle, record.baseline_revision ), proposal_type=rsi.ProposalType.SYSTEM_MESSAGE_PATCH,
            rationale=rsi.Rationale( failure_type=rsi.FailureType.WRONG_ANSWER, evidence_trace_ids=[], summary=proposal.patch.summary,
                                     expected_benefit=proposal.patch.expected_benefit ),
            change_set=rsi.ChangeSet( changes=[ rsi.ArtifactChange(
                artifact_type=rsi.ArtifactType.SYSTEM_MESSAGE, target_revision=_sm( cycle, record.baseline_revision ),
                proposal_type=rsi.ProposalType.SYSTEM_MESSAGE_PATCH, patch_text=diff[ :20000 ],
                before_excerpt=proposal.patch.find[ :2000 ], after_excerpt=proposal.patch.replace[ :2000 ])],
                produces_wrapper_revision=wrapper or None ),
            expected_benefit=proposal.patch.expected_benefit or proposal.patch.summary,
            risk_level=rsi.RiskLevel( cycle.profile.risk_level ), required_tests=list( cycle.profile.required_tests ))
        proposal.proposal_id = store.improvements.save_proposal( saved )
        probe.progress( f"proposal { proposal.proposal_id }: " + ( f"candidate { wrapper }, { diff.count( chr( 10 ))} diff lines"
                                                                  if not problems else "refused: " + "; ".join( problems )))


class ExperimentPhase( _Phase ):
    key, name = "experiment", "run_experiment"
    purpose = "Run baseline and candidate over the training, regression and held-out tasks; the judge scores both arms."
    description = "Run the baseline-vs-candidate experiment"

    def work( self, probe: Any ) -> None:
        cycle, record = self.cycle, self.cycle.record
        if not record.proposal or not record.proposal.valid:
            probe.progress( "no valid candidate: nothing to experiment on; the gates will say why" )
            return
        runner = cycle.runner()
        groups = { "training": cycle.tasks( TaskRole.TRAINING, TaskRole.REGRESSION ),
                   "held_out": cycle.tasks( TaskRole.HELD_OUT )}
        scores: list[ ArmScore ] = []
        record.regressions, record.improvements = [], []
        record.arm_tokens = { "baseline": 0, "candidate": 0 }      # a resumed experiment starts its count again
        for group, tasks in groups.items():
            if not tasks:
                continue
            requests = [ rsi.RunRequest( run_id=f"{ record.cycle_id }:{ group }:{ t.name }", agent_name=cycle.profile.name,
                                         task_name=t.name, input_text=t.input_text,
                                         wrapper_revision=record.baseline_revision, metadata={ "llm_name": record.model })
                         for t in tasks ]
            experiment = rsi.BaselineCandidateExperimentRunner(
                runner=runner, evaluator=_Judge( cycle ), llm_name=record.model ).run( rsi.ABRunConfig(
                    agent_name=cycle.profile.name, baseline_wrapper_revision=record.baseline_revision,
                    candidate_wrapper_revision=record.candidate_revision, task_inputs=requests ))
            record.experiment_ids[ group ] = cycle.store.improvements.save_experiment( experiment )
            names = { str( i ): t.name for i, t in enumerate( tasks )}
            record.regressions  += [ f"{ group }: " + _named( r, names ) for r in experiment.regressions ]
            record.improvements += [ f"{ group }: " + _named( r, names ) for r in experiment.improvements ]
            for arm, cards in (( "baseline", experiment.baseline_scorecards ), ( "candidate", experiment.candidate_scorecards )):
                for task, card in zip( tasks, cards ):
                    scores.append( ArmScore( task=task.name, role=task.role.value, arm=arm, passed=card.passed_checks,
                                             failed=card.failed_checks, notes=( card.notes or "" )[ :300 ]))
            cycle.save()
        record.scores = scores
        probe.progress( f"{ len( record.improvements )} improvements, { len( record.regressions )} regressions" )


class _Judge( rsi.IEvaluator ):
    """The profile's judge, with each trace also saved, so every experiment run is in the trace table."""

    def __init__( self, cycle: Cycle ):
        self._cycle = cycle

    def evaluate( self, trace: rsi.TraceRecord ) -> rsi.ScoreCard:
        trace.project_name = "agent_improvement"
        trace.id = self._cycle.store.traces.save_trace( trace )
        card = self._cycle.profile.evaluator.evaluate( trace )
        record = self._cycle.record
        arm = "candidate" if trace.system_message_revision == record.candidate_revision else "baseline"
        record.arm_tokens[ arm ] = record.arm_tokens.get( arm, 0 ) + int( trace.token_usage.total_tokens )
        usage = next(( c for c in trace.tool_calls if c.tool_name == "usage.estimate" ), None )
        if usage:
            record.token_source = json.loads( usage.arguments_json ).get( "source", "" )
        transport = not trace.agent_output and not any( c.tool_name.startswith( "route." ) for c in trace.tool_calls )
        return card.model_copy( update={ "failed_checks": card.failed_checks + ([ "transport" ] if transport else [])})


def _named( text: str, names: dict[ str, str ]) -> str:
    return re.sub( r"case (\d+)", lambda m: f"task { names.get( m.group( 1 ), m.group( 1 ))}", text )


class GatesPhase( _Phase ):
    key, name = "gates", "apply_gates"
    purpose = "Recheck the judge, run the gate chain, and decide who must approve: a standing policy or a person."
    description = "Apply the promotion gates"

    def work( self, probe: Any ) -> None:
        cycle, record = self.cycle, self.cycle.record
        held = [ s for s in record.scores if s.role == TaskRole.HELD_OUT.value ]
        context = {
            "proposal_id": record.proposal.proposal_id if record.proposal else None,
            "candidate_revision": record.candidate_revision,
            "patch_problems": ( record.proposal.problems if record.proposal else [ "no proposal" ]),
            "judge_fingerprint_recorded": record.judge_fingerprint,
            "judge_fingerprint_now": cycle.profile.judge_fingerprint(),
            "risk_level": cycle.profile.risk_level, "required_tests": cycle.profile.required_tests,
            "baseline_cost_usd": "0", "candidate_cost_usd": "0",
            "baseline_tokens": record.arm_tokens.get( "baseline", 0 ),
            "candidate_tokens": record.arm_tokens.get( "candidate", 0 ),
            "token_source": record.token_source,
            "regressions": [ r for r in record.regressions if r.startswith( "training:" )],
            "improvements": [ r for r in record.improvements if r.startswith( "training:" )],
            "held_out_ran": bool( held ),
            "held_out_regressions": [ r for r in record.regressions if r.startswith( "held_out:" )],
            "held_out_improvements": [ r for r in record.improvements if r.startswith( "held_out:" )],
        }
        result = gate_chain().evaluate( context )
        ( cycle.directory / "gate_result.json" ).write_text( result.model_dump_json( indent=1 ), encoding="utf-8" )
        record.gates = [ GateOutcome( name=g.gate_name, decision=g.decision.value, reason=g.reason ) for g in result.results ]
        record.gate_decision = result.final_decision.value
        probe.progress( " | ".join( f"{ g.name }={ g.decision }" for g in record.gates ))
        if result.final_decision == rsi.GateDecision.BLOCK:
            _stop( cycle, CycleState.REJECTED, f"blocked by the { result.blocked_by } gate" )
            return
        standing = cycle.profile.promotion.standing_approver( cycle.environment.name )
        if result.final_decision == rsi.GateDecision.ALLOW and standing and not cycle.environment.requires_person:
            record_decision( cycle, Decision( approved=True, approver=standing, key=f"standing:{ record.cycle_id }",
                                              reason=f"every gate allowed; standing policy for the { cycle.environment.name } environment" ))
            return
        why = "a gate asked for a person" if result.final_decision == rsi.GateDecision.NEEDS_HUMAN else \
              f"the { cycle.environment.name } environment needs a named person"
        record.state, record.reason = CycleState.AWAITING_APPROVAL, f"waiting for a decision: { why }"
        probe.waiting( f"{ record.reason }. Run: adw_improvement_ops.py decide --cycle-id { record.cycle_id } "
                       f"--approve|--reject --approver <name> --key <unique>", cycle.profile.budgets.approval_wait_seconds )


class ActivatePhase( _Phase ):
    key, name = "activate", "activate_and_verify"
    purpose = "With an approval the policy accepts, promote through the RSI activation service, write, and verify what the target loads."
    description = "Activate the candidate and verify the loaded revision"

    def work( self, probe: Any ) -> None:
        cycle, record, store, agent = self.cycle, self.cycle.record, self.cycle.store, self.cycle.profile.name
        decision = record.decision
        if decision is None or not decision.approved:
            raise RuntimeError( "activation reached without an approval" )      # the phase order should make this impossible
        allowed, why = cycle.profile.promotion.may_approve( decision.approver, cycle.environment.name )
        if not allowed:
            _stop( cycle, CycleState.REJECTED, why )
            return
        if cycle.profile.judge_fingerprint() != record.judge_fingerprint:
            _stop( cycle, CycleState.REJECTED, "the judge changed between the gates and activation" )
            return
        candidate_body = store.body( agent, _sm( cycle, record.candidate_revision ))
        previous_body  = store.body( agent, _sm( cycle, record.baseline_revision ))
        active = store.revisions.get_active_revision( agent )
        if active is None or active.wrapper_revision != record.candidate_revision:
            gate_result = rsi.GateChainResult.model_validate_json(( cycle.directory / "gate_result.json" ).read_text( encoding="utf-8" ))
            outcome = rsi.ActivationService( wrapper_repository=store.revisions, improvement_repository=store.improvements,
                                             approval_gateway=store.approvals, gate_result=gate_result
                                             ).try_activate( store.improvements.get_proposal( record.proposal.proposal_id ))
            if not outcome.activated:
                _stop( cycle, CycleState.REJECTED, f"the activation service refused: { outcome.reason }" )
                return
            probe.progress( f"activation record: { outcome.previous_wrapper_revision } -> { outcome.candidate_wrapper_revision }" )
        else:
            probe.progress( "the candidate is already the active revision (resumed); verifying only" )
        try:
            if sha256( cycle.environment.read()) != sha256( candidate_body ):
                cycle.environment.write( candidate_body )
            cycle.on_activation_write()
            loaded, matches = cycle.environment.loaded( candidate_body )
            if not matches:
                raise RuntimeError( f"the target loaded { loaded[ :12 ]}, not the candidate" )
        except Exception as error:                                   # noqa: BLE001 -- any failure here means put it back
            probe.progress( f"activation failed verification ({ error }); rolling back" )
            rollback( cycle, f"automatic: { error }", previous_body )
            return
        record.activated_revision, record.loaded_sha256 = record.candidate_revision, loaded
        record.state, record.reason = CycleState.ACTIVATED, f"activated { record.candidate_revision }; the target loads it"
        probe.progress( f"{ cycle.environment.name } now loads { record.candidate_revision } (sha { loaded[ :12 ]})" )


class SettlePhase( _Phase ):
    key, name = "settle", "reject_or_roll_back"
    purpose = "For a rejected candidate, mark the proposal rejected and confirm the baseline is untouched."
    description = "Settle a rejection: mark the proposal rejected, confirm the baseline is untouched"
    runs_in = frozenset({ CycleState.REJECTED, CycleState.ACTIVATED, CycleState.ROLLED_BACK })

    def work( self, probe: Any ) -> None:
        cycle, record = self.cycle, self.cycle.record
        if record.state == CycleState.ACTIVATED:
            probe.progress( "nothing to reject: the candidate is active. Rollback stays available (adw_improvement_ops.py rollback)" )
            return
        if record.state == CycleState.ROLLED_BACK:
            probe.progress( f"already rolled back: { record.rollback.get( 'reason', '' )}" )
            return
        if record.proposal and record.proposal.proposal_id:
            cycle.store.improvements.update_proposal_status( record.proposal.proposal_id, rsi.ProposalStatus.REJECTED )
        on_disk = sha256( cycle.environment.read())
        active  = cycle.store.revisions.get_active_revision( cycle.profile.name )
        untouched = on_disk == record.baseline_sha256 and active is not None and active.wrapper_revision == record.baseline_revision
        record.rollback[ "baseline_untouched" ] = untouched
        probe.progress( "baseline untouched" if untouched else "WARNING: the environment no longer holds the baseline" )


class ReportPhase( _Phase ):
    key, name = "report", "record_and_report"
    purpose = "Write the cycle's report: lineage, revisions, budgets, scores, gates, decision and what the target loads."
    description = "Write the cycle report: lineage, scores, gates, decision"
    runs_in = frozenset( CycleState )

    def perform( self, probe: Any ) -> None:
        # Always rewritten, so a report exists at every stop (awaiting a decision included).
        self.cycle.probe = probe
        path = self.cycle.directory / "report.md"
        path.write_text( render_report( self.cycle.record ), encoding="utf-8" )
        self.cycle.record.report_path = str( path )
        if self.cycle.record.terminal() and not self.cycle.record.done( self.key ):
            self.cycle.record.completed.append( self.key )
        self.cycle.save()
        probe.progress( f"{ self.cycle.record.state.value }: { self.cycle.record.reason } -- report { path }" )


PHASES = ( ResolvePhase, EvidencePhase, BaselinePhase, ProposePhase, PreparePhase,
           ExperimentPhase, GatesPhase, ActivatePhase, SettlePhase, ReportPhase )


# --------------------------------------------------------------------------- operations on a cycle

def record_decision( cycle: Cycle, decision: Decision ) -> str:
    """Record an approval or rejection once. A repeat with the same key is a no-op; a different second decision is refused."""
    record = cycle.record
    if record.decision is not None:
        if record.decision.key == decision.key or ( record.decision.approved == decision.approved
                                                    and record.decision.approver == decision.approver ):
            return "already recorded"
        raise ValueError( f"a decision is already recorded ({ record.decision.approver }, approved={ record.decision.approved })" )
    if decision.approved:
        allowed, why = cycle.profile.promotion.may_approve( decision.approver, cycle.environment.name )
        if not allowed:
            raise PermissionError( why )
    if record.proposal and record.proposal.proposal_id:
        snapshot = ( cycle.directory / "gate_result.json" )
        cycle.store.approvals.record_decision( rsi.ApprovalRecord(
            proposal_id=record.proposal.proposal_id, approved=decision.approved, approver=decision.approver,
            reason=decision.reason, candidate_wrapper_revision=record.candidate_revision,
            gate_result_snapshot_json=snapshot.read_text( encoding="utf-8" ) if snapshot.exists() else None ))
    record.decision = decision
    if record.state == CycleState.AWAITING_APPROVAL:
        waited = ( datetime.fromisoformat( decision.at ) - datetime.fromisoformat( record.updated_at )).total_seconds()
        record.rollback[ "_approval_wait_seconds" ] = record.rollback.get( "_approval_wait_seconds", 0.0 ) + max( waited, 0.0 )
        record.state = CycleState.RUNNING if decision.approved else CycleState.REJECTED
        record.reason = f"{ 'approved' if decision.approved else 'rejected' } by { decision.approver }"
    cycle.save()
    return "recorded"


def rollback( cycle: Cycle, reason: str, previous_body: str | None = None ) -> dict[ str, Any ]:
    """Put the previous revision back -- in the RSI store, in the environment, and in what the target loads."""
    record, store, agent = cycle.record, cycle.store, cycle.profile.name
    if record.state == CycleState.ROLLED_BACK:
        return record.rollback
    outcome = store.rollbacks.rollback( agent, reason )
    restored = outcome.candidate_wrapper_revision or record.baseline_revision
    body = previous_body if previous_body is not None else store.body( agent, _sm( cycle, restored ))
    cycle.environment.write( body )
    loaded, matches = cycle.environment.loaded( body )
    if record.proposal and record.proposal.proposal_id:
        store.improvements.update_proposal_status( record.proposal.proposal_id, rsi.ProposalStatus.ROLLED_BACK )
    record.rollback.update({ "reason": reason, "restored_revision": restored, "loaded_sha256": loaded,
                             "verified": matches, "rolled_back_from": outcome.previous_wrapper_revision or record.candidate_revision })
    record.state, record.loaded_sha256 = CycleState.ROLLED_BACK, loaded
    record.activated_revision = ""
    record.reason = f"rolled back to { restored } ({ reason }); target loads it: { matches }"
    cycle.save()
    return record.rollback


def request_cancel( cycles: FileCycleStore, cycle_id: str, by: str ) -> str:
    record = cycles.load( cycle_id )
    if record.terminal():
        return f"already { record.state.value }"
    ( cycles.directory( cycle_id ) / "cancel.requested" ).write_text( by, encoding="utf-8" )
    if record.state == CycleState.AWAITING_APPROVAL:            # nothing is running: settle it here
        record.state, record.reason = CycleState.CANCELLED, f"cancelled by { by } while waiting for a decision"
        cycles.save( record )
        return "cancelled"
    return "cancel requested; the running phase stops at its next check"


def render_report( record: CycleRecord ) -> str:
    def rows( items: list[ str ]) -> str:
        return "\n".join( f"- { i }" for i in items ) or "- none"
    scores = "\n".join( f"| { s.task } | { s.role } | { s.arm } | { len( s.passed )} | { ', '.join( s.failed ) or '-' } |"
                        for s in ( record.baseline + record.scores ))
    gates = "\n".join( f"| { g.name } | { g.decision } | { g.reason } |" for g in record.gates )
    patch = record.proposal.patch if record.proposal else None
    return f"""# Improvement cycle { record.cycle_id }

- target: **{ record.target }** in the **{ record.environment }** environment, generation { record.generation }
  (parent: { record.parent_cycle or 'none' }), attempt { record.attempt }
- state: **{ record.state.value }** -- { record.reason }
- failed attempts: { '; '.join( e.splitlines()[ 0 ] for e in record.attempt_errors ) or 'none' }
- started { record.started_at }, updated { record.updated_at }; ADW runs: { ', '.join( record.adw_ids ) or '-' }
- baseline { record.baseline_revision } (sha { record.baseline_sha256[ :12 ]}); candidate { record.candidate_revision or '-' };
  activated { record.activated_revision or '-' }; target loads sha { record.loaded_sha256[ :12 ] or '-' }
- judge fingerprint { record.judge_fingerprint[ :16 ]}; suite { record.suite_version }; worker model { record.model }
- budgets: { record.worker_runs }/{ record.budgets.max_worker_runs } worker runs, { record.estimated_tokens }/{ record.budgets.max_estimated_tokens }
  estimated tokens (prompt_chars/4 when the model reports none)
- simulated faults: { ', '.join( record.simulated_faults ) or 'none' }

## Proposal

{ ( f"{ patch.summary }  (author { patch.author }; expected: { patch.expected_benefit })" ) if patch else "none" }
{ ( "Refused by the change policy: " + "; ".join( record.proposal.problems )) if record.proposal and record.proposal.problems else "" }
Diff: { record.proposal.diff_path if record.proposal else '-' }

## Scores

| task | role | arm | passed | failed |
| --- | --- | --- | --- | --- |
{ scores }

Improvements:
{ rows( record.improvements )}

Regressions:
{ rows( record.regressions )}

## Gates ({ record.gate_decision or 'not run' })

| gate | decision | reason |
| --- | --- | --- |
{ gates }

## Decision

{ ( f"{ 'approved' if record.decision.approved else 'rejected' } by { record.decision.approver } at { record.decision.at }: { record.decision.reason }" ) if record.decision else "none" }

## Rollback

{ json.dumps({ k: v for k, v in record.rollback.items() if not k.startswith( '_' )}, indent=1 ) if record.rollback else "none" }

## Bridge tasks

{ rows( record.bridge_tasks )}
"""

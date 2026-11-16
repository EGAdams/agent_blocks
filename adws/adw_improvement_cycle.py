#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Improvement Cycle -- one bounded, observable self-improvement cycle of one target agent.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_improvement_cycle.py \\
        --target sydney --environment pilot [--improver claude|file --proposal-file p.json] \\
        [--cycle-id <id>] [--parent-cycle <id>] [--request-key <unique>] [--requested-by EG] [--json]

Phases (each one the engine's, wrapped for the live sequence debugger):

    resolve -> evidence -> baseline -> propose -> prepare -> experiment -> gates
            -> activate -> settle -> report

A cycle that stops to wait for a person ends this run with state awaiting_approval; after
`adw_improvement_ops.py decide ...`, run this again with the same --cycle-id and it resumes the same
recorded cycle (attempt + 1), skipping every phase already done.

Test-only fault controls, each naming a phase key, all recorded on the cycle as simulated:

    --fail-at <key>                      raise inside that phase (the cycle stays resumable)
    --stall-at <key> --stall-seconds N   go silent for N seconds, then continue
    --kill-at <key>                      exit the process abruptly, no cleanup
    --simulate activation-verify-mismatch
                                         after the activation write, make the loaded-revision check
                                         fail, so the automatic rollback runs

The engine is `agent_improvement/` and is callable without this file. Read agent_improvement/DESIGN.md first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))

from sssf_roles import ( IApplication, IConfigLoader, ILifecycleLog, IPhaseExecutor, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from debug_pilot.events import SssfDebugEvents
from debug_pilot.faults import NoFaults, PilotFault, PlannedFaults
from debug_pilot.interfaces import FaultPlan, IFaultInjector
from debug_pilot.monitored_phase import MonitoredPhase

from agent_improvement.improvers import ClaudeCliImprover, FileImprover
from agent_improvement.models import CycleState
from agent_improvement.observe import RedactingEvents
from agent_improvement.profiles import build_profile, data_dir
from agent_improvement.service import ACCEPTED, Refused, Workspace, open_cycle

SIMULATIONS = ( "activation-verify-mismatch", )


class RealSleeper:
    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )


class SimulatedVerifyMismatch( RuntimeError ):
    """Labelled: raised only when --simulate activation-verify-mismatch was given."""


def _answer( args: argparse.Namespace, ok: bool, line: str, data: dict, adw_id: str ) -> None:
    answer = { "ok": ok, "verified": ok, "line": line, "data": data, "adw_id": adw_id, "retryable": False }
    if args.json_out:
        Path( args.json_out ).write_text( json.dumps( answer, indent=1 ), encoding="utf-8" )
    print( json.dumps( answer ) if args.json else line )


class CycleApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor

    def run( self, argv: list[ str ]) -> int:
        args = _parse( argv )
        if args.environment == "live" and os.environ.get( "AGENT_IMPROVEMENT_LIVE" ) != "1":
            # The feature switch: a live cycle exists only when the switch is on.
            _answer( args, False, "live improvement is switched off (AGENT_IMPROVEMENT_LIVE=1 turns it on)", {}, "" )
            return 1
        profile = build_profile( args.target )
        ws      = Workspace( profile, data_dir( args.environment ))
        cfg     = self._config_loader.load( args.config )
        self._config_loader.validate( cfg, [])
        run     = self._session_factory.create( cfg, args.adw_id )
        simulated = [ f"simulate:{ s }" for s in args.simulate ] + [
            f"{ name }:{ value }" for name, value in (( "fail_at", args.fail_at ), ( "stall_at", args.stall_at ),
                                                       ( "kill_at", args.kill_at )) if value ]
        try:
            record, how = open_cycle( ws, environment=args.environment, adw_id=run.adw_id, cycle_id=args.cycle_id,
                                      parent_cycle=args.parent_cycle, request_key=args.request_key,
                                      requested_by=args.requested_by, simulated_faults=simulated )
        except ( Refused, KeyError ) as error:
            _answer( args, False, f"refused: { error }", {}, run.adw_id )
            return run.finish( accepted=False, reason=str( error ))
        data = { "cycle_id": record.cycle_id, "how": how, "adw_id": run.adw_id, "task_id": args.task_id }
        if how == "duplicate":
            _answer( args, True, f"cycle { record.cycle_id } already exists ({ record.state.value }); nothing run",
                     { **data, "state": record.state.value }, run.adw_id )
            return run.finish( accepted=True, reason="duplicate request: existing cycle returned" )
        if not ws.cycles.acquire( record.cycle_id, profile.budgets.max_concurrent_cycles ):
            _answer( args, False, f"another { profile.name } cycle is running; try again when it stops", data, run.adw_id )
            return run.finish( accepted=False, reason="concurrency limit" )
        try:
            return self._phases( args, ws, record, run, data )
        finally:
            ws.cycles.release( record.cycle_id )

    def _phases( self, args: argparse.Namespace, ws: Workspace, record, run, data: dict ) -> int:
        improver = ( FileImprover( Path( args.proposal_file ), author=args.requested_by or "file" )
                     if args.improver == "file" else ClaudeCliImprover( model=args.improver_model ))
        on_write = None
        if "activation-verify-mismatch" in args.simulate:
            def on_write() -> None:
                raise SimulatedVerifyMismatch( "SIMULATED FAULT: the loaded-revision check was made to fail" )
        cycle  = ws.cycle( record, improver, on_write )
        events = RedactingEvents( SssfDebugEvents( run ), { "cycle_id": record.cycle_id, "target": record.target,
                                                           "environment": record.environment,
                                                           "bridge_task": args.task_id, "request_key": args.request_key })
        faults: IFaultInjector = _faults( args )
        with ExitStack() as cleanup:
            phases = [ MonitoredPhase( step, run.adw_id, events, faults, attempt=record.attempt )
                       for step in cycle.phases()]
            for phase in phases:
                cleanup.callback( phase.close )
            try:
                for phase in phases:
                    self._phase_executor.execute( run, phase )
            except PilotFault as fault:
                _answer( args, False, f"cycle { record.cycle_id } stopped by a test fault: { fault }; "
                                      f"rerun with --cycle-id { record.cycle_id } to resume",
                         { **data, "state": cycle.record.state.value }, run.adw_id )
                return run.finish( accepted=False, reason=str( fault ))
            except Exception as error:                       # noqa: BLE001 -- recorded, the cycle stays resumable
                # A decided cycle keeps its decision's reason; the error is kept beside it, not over it.
                failure = f"attempt { record.attempt } failed: { str( error )[ :500 ]}"
                cycle.record.attempt_errors.append( failure )
                if cycle.record.state == CycleState.RUNNING:
                    cycle.record.reason = failure
                cycle.save()
                _answer( args, False, f"cycle { record.cycle_id }: { error }", { **data, "state": cycle.record.state.value },
                         run.adw_id )
                return run.finish( accepted=False, reason=str( error ))
        final = cycle.record
        ok = final.state in ACCEPTED
        line = f"cycle { final.cycle_id } { final.state.value }: { final.reason }"
        if final.state == CycleState.AWAITING_APPROVAL:
            line += ( f". Decide: adw_improvement_ops.py decide --target { final.target } --environment "
                      f"{ final.environment } --cycle-id { final.cycle_id } --approve|--reject --approver EG --key <unique>" )
        _answer( args, ok, line, { **data, "state": final.state.value, "baseline": final.baseline_revision,
                                   "candidate": final.candidate_revision, "activated": final.activated_revision,
                                   "gate_decision": final.gate_decision, "report": final.report_path }, run.adw_id )
        return run.finish( accepted=ok, reason=line )


def _faults( args: argparse.Namespace ) -> IFaultInjector:
    plan = FaultPlan( fail_at=args.fail_at, stall_at=args.stall_at, stall_seconds=args.stall_seconds, kill_at=args.kill_at )
    return PlannedFaults( plan, RealSleeper()) if ( plan.fail_at or plan.stall_at or plan.kill_at ) else NoFaults()


def _parse( argv: list[ str ]) -> argparse.Namespace:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--target", default="sydney" )
    parser.add_argument( "--environment", choices=( "pilot", "live" ), default="pilot" )
    parser.add_argument( "--cycle-id", default="", help="resume this cycle, or name a new one" )
    parser.add_argument( "--parent-cycle", default="", help="the finished cycle this one follows (lineage)" )
    parser.add_argument( "--request-key", default="", help="idempotency key: a repeat returns the same cycle" )
    parser.add_argument( "--requested-by", default="" )
    parser.add_argument( "--task-id", default="", help="the Bridge task that carried the request, for correlation" )
    parser.add_argument( "--improver", choices=( "claude", "file" ), default="claude" )
    parser.add_argument( "--improver-model", default="sonnet" )
    parser.add_argument( "--proposal-file", default="" )
    parser.add_argument( "--fail-at", default="", help="TEST ONLY: phase key that raises" )
    parser.add_argument( "--stall-at", default="", help="TEST ONLY: phase key that goes silent" )
    parser.add_argument( "--stall-seconds", type=float, default=0.0 )
    parser.add_argument( "--kill-at", default="", help="TEST ONLY: phase key where the process exits abruptly" )
    parser.add_argument( "--simulate", action="append", default=[], choices=SIMULATIONS, help="TEST ONLY, labelled" )
    parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
    parser.add_argument( "--adw-id", default=None )
    parser.add_argument( "--json", action="store_true" )
    parser.add_argument( "--json-out", default="" )
    args = parser.parse_args( argv )
    if args.improver == "file" and not args.proposal_file:
        parser.error( "--improver file needs --proposal-file" )
    return args


def build_application() -> IApplication:
    log: ILifecycleLog = PrintLifecycleLog()
    return CycleApplication( config_loader   = SssfConfigLoader( log ),
                             session_factory = SessionRunFactory( log ),
                             phase_executor  = ManualContextPhaseExecutor( log ),
                             logger          = log )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

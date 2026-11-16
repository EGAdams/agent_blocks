#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Improvement Ops -- what a person (or the Bridge) does to an improvement cycle, one phase per run.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_improvement_ops.py <operation> \\
        --target sydney --environment pilot [--cycle-id <id>] [--json]

    status      state, phases done, revisions, budgets, health
    proposal    the patch, its summary and the candidate diff
    evaluation  baseline and candidate scores, held-out results, regressions, gates
    report      the cycle's report.md
    lineage     the chain of generations ending at --cycle-id (or every cycle of the target)
    health      running | waiting_for_person | stalled | no_progress | finished
    decide      --approve|--reject --approver <name> --key <unique> [--reason ...]
    cancel      --by <name>
    rollback    --by <name> --reason <text>

Each run is one SSSF session with one phase, so the ADW debugger shows every decision, cancel and rollback.
The operations themselves are `agent_improvement.service`, callable without this file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))

from sssf_roles import ( IApplication, IConfigLoader, ILifecycleLog, IPhaseExecutor, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from debug_pilot.events import SssfDebugEvents
from debug_pilot.faults import NoFaults
from debug_pilot.monitored_phase import MonitoredPhase

from agent_improvement import service
from agent_improvement.observe import RedactingEvents
from agent_improvement.profiles import build_profile, data_dir

READS  = ( "status", "proposal", "evaluation", "report", "lineage", "health" )
WRITES = ( "decide", "cancel", "rollback" )


class OperationStep:
    """An IPilotStep holding one service call; its answer is kept for the caller."""

    def __init__( self, operation: str, call ):
        self.key, self.name = operation, f"improvement_{ operation }"
        self.purpose     = f"Run the { operation } operation on an improvement cycle"
        self.description = self.purpose
        self._call = call
        self.answer: dict = {}
        self.error = ""

    def perform( self, probe ) -> None:
        try:
            self.answer = self._call()
        except ( service.Refused, KeyError ) as error:
            self.error = str( error )
            probe.progress( f"refused: { error }" )
            return
        probe.progress( json.dumps({ k: v for k, v in self.answer.items() if k not in ( "diff", "report" )})[ :600 ])

    def dispose( self ) -> None:
        return None


def _call( args: argparse.Namespace, ws: service.Workspace ):
    op, cid = args.operation, args.cycle_id
    return {
        "status":     lambda: service.status( ws, cid ),
        "proposal":   lambda: service.proposal( ws, cid ),
        "evaluation": lambda: service.evaluation( ws, cid ),
        "report":     lambda: service.report( ws, cid ),
        "lineage":    lambda: service.lineage( ws, cid ),
        "health":     lambda: service.health( ws, cid ),
        "decide":     lambda: service.decide( ws, cid, approved=args.approve, approver=args.approver,
                                              key=args.key, reason=args.reason ),
        "cancel":     lambda: service.cancel( ws, cid, args.by ),
        "rollback":   lambda: service.roll_back( ws, cid, by=args.by, reason=args.reason or "rollback requested" ),
    }[ op ]


def _line( op: str, answer: dict ) -> str:
    if op == "proposal":
        return ( f"proposal { answer.get( 'proposal_id' )}: { answer.get( 'summary', 'none' )}\n{ answer.get( 'diff', '' )}" )
    if op == "report":
        return answer.get( "report", "" )
    if op == "lineage":
        rows = [ f"  gen { c[ 'generation' ]}  { c[ 'cycle_id' ]}  { c[ 'state' ]}  { c[ 'baseline' ]} -> "
                 f"{ c[ 'candidate' ] or '-' }  activated { c[ 'activated' ] or '-' }  restored { c[ 'restored' ] or '-' }"
                 for c in answer[ "cycles" ]]
        return "\n".join([ f"{ answer[ 'target' ]}: active { answer[ 'active_revision' ]}", *rows,
                           f"  next: generation { answer[ 'next_generation' ]} "
                           f"({ 'allowed' if answer[ 'next_allowed' ] else 'not allowed' }, limit { answer[ 'max_generations' ]})" ])
    return json.dumps( answer, indent=1 )


class OpsApplication( LifecycleLogged, IApplication ):
    def __init__( self, config_loader: IConfigLoader, session_factory: ISessionFactory,
                  phase_executor: IPhaseExecutor, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._config_loader, self._session_factory, self._phase_executor = config_loader, session_factory, phase_executor

    def run( self, argv: list[ str ]) -> int:
        args = _parse( argv )
        ws   = service.Workspace( build_profile( args.target ), data_dir( args.environment ))
        cfg  = self._config_loader.load( args.config )
        self._config_loader.validate( cfg, [])
        run  = self._session_factory.create( cfg, args.adw_id )
        step = OperationStep( args.operation, _call( args, ws ))
        events = RedactingEvents( SssfDebugEvents( run ), { "cycle_id": args.cycle_id, "target": ws.profile.name,
                                                           "environment": args.environment, "bridge_task": args.task_id,
                                                           "request_key": args.key })
        phase = MonitoredPhase( step, run.adw_id, events, NoFaults())
        try:
            self._phase_executor.execute( run, phase )
        finally:
            phase.close()
        ok = not step.error
        line = f"refused: { step.error }" if step.error else _line( args.operation, step.answer )
        answer = { "ok": ok, "verified": ok, "line": line if args.operation not in ( "proposal", "report" ) else line[ :2000 ],
                   "data": step.answer, "adw_id": run.adw_id, "retryable": False }
        if args.json_out:
            Path( args.json_out ).write_text( json.dumps( answer, indent=1 ), encoding="utf-8" )
        print( json.dumps( answer ) if args.json else line )
        return run.finish( accepted=ok, reason=line[ :300 ])


def _parse( argv: list[ str ]) -> argparse.Namespace:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "operation", choices=READS + WRITES )
    parser.add_argument( "--target", default="sydney" )
    parser.add_argument( "--environment", choices=( "pilot", "live" ), default="pilot" )
    parser.add_argument( "--cycle-id", default="" )
    decision = parser.add_mutually_exclusive_group()
    decision.add_argument( "--approve", action="store_true" )
    decision.add_argument( "--reject", action="store_true" )
    parser.add_argument( "--approver", default="" )
    parser.add_argument( "--key", default="", help="idempotency key for decide" )
    parser.add_argument( "--reason", default="" )
    parser.add_argument( "--by", default="" )
    parser.add_argument( "--task-id", default="" )
    parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
    parser.add_argument( "--adw-id", default=None )
    parser.add_argument( "--json", action="store_true" )
    parser.add_argument( "--json-out", default="" )
    args = parser.parse_args( argv )
    if args.operation != "lineage" and not args.cycle_id:
        parser.error( f"{ args.operation } needs --cycle-id" )
    if args.operation == "decide" and not ( args.approve or args.reject ):
        parser.error( "decide needs --approve or --reject" )
    if args.operation in ( "cancel", "rollback" ) and not args.by:
        parser.error( f"{ args.operation } needs --by <name>" )
    return args


def build_application() -> IApplication:
    log: ILifecycleLog = PrintLifecycleLog()
    return OpsApplication( SssfConfigLoader( log ), SessionRunFactory( log ), ManualContextPhaseExecutor( log ), log )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

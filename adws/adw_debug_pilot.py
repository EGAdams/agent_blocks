#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Debug Pilot -- five small phases that announce their own lifecycle, for the dashboard's live sequence debugger.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_debug_pilot.py \\
        [--pace 1.0] [--torture] [--adw-id a1b2c3d4]

Test-only fault controls, each naming a step key
(prepare, lookup-1, lookup-2, wait, report):

    --fail-at lookup-2                   raise inside that phase
    --stall-at wait --stall-seconds 20   go silent for that long, then recover
    --kill-at lookup-1                   exit the process abruptly, no cleanup

Phases: code(prepare) -> code(lookup) -> code(lookup) -> code(wait_for_gate) -> code(report)

Read adws/debug_pilot/DESIGN.md before changing it.
"""
from __future__ import annotations

import argparse
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
from debug_pilot.interfaces import FaultPlan, IDebugEvents, IFaultInjector, IPilotStep, ISleeper
from debug_pilot.monitored_phase import MonitoredPhase
from debug_pilot.steps import TORTURE, LookupStep, PrepareStep, ReportStep, WaitStep


class RealSleeper:
    def sleep( self, seconds: float ) -> None:
        time.sleep( seconds )


def pilot_steps( sleeper: ISleeper, pace: float, torture: bool ) -> list[ IPilotStep ]:
    beta = TORTURE if torture else "beta: second item"
    return [ PrepareStep( "prepare",  sleeper, pace ),
             LookupStep(  "lookup-1", sleeper, pace, "alpha", "alpha: first item" ),
             LookupStep(  "lookup-2", sleeper, pace, "beta",  beta ),
             WaitStep(    "wait",     sleeper, pace ),
             ReportStep(  "report",   sleeper, pace )]


class PilotApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  sleeper:         ISleeper,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._sleeper         = sleeper

    def run( self, argv: list[ str ]) -> int:
        args = _parse( argv )
        cfg  = self._config_loader.load( args.config )
        self._config_loader.validate( cfg, [])
        run  = self._session_factory.create( cfg, args.adw_id )

        events: IDebugEvents   = SssfDebugEvents( run )
        faults: IFaultInjector = _faults( args, self._sleeper )
        with ExitStack() as cleanup:
            # Every phase object exists before the first one runs, so the ones
            # still to come are real waiting instances, not a static list.
            phases = [ MonitoredPhase( step, run.adw_id, events, faults )
                       for step in pilot_steps( self._sleeper, args.pace, args.torture )]
            for phase in phases:
                cleanup.callback( phase.close )
            try:
                for phase in phases:
                    self._phase_executor.execute( run, phase )
            except PilotFault as fault:
                return run.finish( accepted=False, reason=str( fault ))
        return run.finish( accepted=True, reason="pilot completed" )


def _faults( args: argparse.Namespace, sleeper: ISleeper ) -> IFaultInjector:
    plan = FaultPlan( fail_at=args.fail_at, stall_at=args.stall_at,
                      stall_seconds=args.stall_seconds, kill_at=args.kill_at )
    return PlannedFaults( plan, sleeper ) if ( plan.fail_at or plan.stall_at or plan.kill_at ) else NoFaults()


def _parse( argv: list[ str ]) -> argparse.Namespace:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--pace", type=float, default=1.0, help="seconds each step takes" )
    parser.add_argument( "--torture", action="store_true", help="put syntax-sensitive text in a tool result" )
    parser.add_argument( "--fail-at", default="", help="TEST ONLY: step key that raises" )
    parser.add_argument( "--stall-at", default="", help="TEST ONLY: step key that goes silent" )
    parser.add_argument( "--stall-seconds", type=float, default=0.0, help="TEST ONLY: how long the silence lasts" )
    parser.add_argument( "--kill-at", default="", help="TEST ONLY: step key where the process exits abruptly" )
    parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
    parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
    return parser.parse_args( argv )


def build_application() -> IApplication:
    log: ILifecycleLog = PrintLifecycleLog()
    return PilotApplication( config_loader   = SssfConfigLoader( log ),
                             session_factory = SessionRunFactory( log ),
                             phase_executor  = ManualContextPhaseExecutor( log ),
                             sleeper         = RealSleeper(),
                             logger          = log )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

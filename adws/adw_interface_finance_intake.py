#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
"""ADW Interface Finance Intake -- one scanned document into the ROL finance books.

The workflow is the finance application's own (`rol_finances/finance_app/intake`):
ten ordinary phases that run without this file. This ADW only makes them
observable -- each becomes an SSSF phase, announced to the live debugger.

    normalize -> classify -> extract -> resolve -> judge -> validate
              -> commit -> project -> verify -> publish

Deterministic code runs first. `judge` asks a model only what the exact
lookups left open (a probable duplicate, a look-alike vendor, a vendor with
several allowed categories) and `validate` refuses any answer outside the
allowed values. Whatever cannot be settled waits for a person as a durable
review item; nothing is committed on a guess. `publish` always runs.

    cd /home/adamsl/agent_blocks && /home/adamsl/rol_finances/.venv/bin/python \\
        adws/adw_interface_finance_intake.py "why" --scan /path/to/scan.jpg \\
        --source "Freezer Scanner" [--doc-kind statement] [--conversation-id ID] \\
        [--dispatched-at 0] [--statement-overrides '{"bank_name":..}'] \\
        [--result-out /tmp/result.json] [--config adws/adw_sssf_config/sssf.config.yaml] \\
        [--adw-id a1b2c3d4]

Exit 0 when the run ended committed or waiting on a person; 1 when it failed
or halted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))
sys.path.insert( 0, os.environ.get( "ROL_FINANCES_ROOT", "/home/adamsl/rol_finances" ))

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams
from adw_modules.debug_events import SssfDebugEvents
from adw_modules.monitored_phase import MonitoredSssfPhase

from architecture.michiko_intake.models import ProcessScanCommand, ScannerId
from finance_app.intake.composition import IntakePorts, build_facade
from finance_app.intake.contracts import IntakeRunResult, Outcome
from finance_app.intake.workflow import IntakeFacade, IntakeRun

REQUIRED_AGENTS: list[ str ] = []   # no SSSF agent: judgment goes through the finance gateway


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class IntakeRequest:
    prompt:              str
    scan:                Path
    source:              str
    doc_kind:            str
    conversation_id:     str
    dispatched_at:       float
    statement_overrides: dict
    result_out:          str
    config:              str
    adw_id:              str | None


class FinancePhaseFailed( RuntimeError ):
    """A finance phase crashed. The run is already settled; this only turns the
    phase red in the debugger."""


# -- Adapter: one finance phase seen as one SSSF phase ------------------------

class FinancePhase( LifecycleLogged, IPhase ):
    def __init__( self, run: IntakeRun, phase, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run = run
        self._phase = phase

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._phase.name, kind="code", owner="finance",
                            description=self._phase.purpose )

    def act( self, handle: IPhaseHandle ) -> str:
        before = len( self._run.state.problems )
        outcome = self._run.run_phase( self._phase )
        state = self._run.state
        handle.log( outcome=outcome, run_id=state.run_id, judgment_calls=state.judgment_calls,
                    settled=state.outcome.value if state.outcome else "" )
        if outcome == "error":
            raise FinancePhaseFailed( "; ".join( state.problems[ before: ]) or self._phase.name )
        return outcome


# -- Argument parsing --------------------------------------------------------

class ArgparseIntakeArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> IntakeRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="process a scanned finance document" )
        parser.add_argument( "--scan", required=True, help="the scanned image or PDF" )
        parser.add_argument( "--source", required=True, help='e.g. "Freezer Scanner"' )
        parser.add_argument( "--doc-kind", default="", help="a person's assertion, e.g. statement" )
        parser.add_argument( "--conversation-id", default="" )
        parser.add_argument( "--dispatched-at", type=float, default=0.0 )
        parser.add_argument( "--statement-overrides", default="{}" )
        parser.add_argument( "--result-out", default="" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None )
        parsed = parser.parse_args( argv )
        return IntakeRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                              scan=Path( parsed.scan ).resolve(), source=parsed.source.strip(),
                              doc_kind=parsed.doc_kind.strip(),
                              conversation_id=parsed.conversation_id,
                              dispatched_at=parsed.dispatched_at,
                              statement_overrides=json.loads( parsed.statement_overrides ),
                              result_out=parsed.result_out, config=parsed.config,
                              adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class IntakeApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  facade_for,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._facade_for      = facade_for

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        facade: IntakeFacade = self._facade_for( request )
        command = ProcessScanCommand(
            scanner_id=ScannerId( value=request.source.split()[ 0 ].lower()),
            scanner_name=request.source, source_path=str( request.scan ),
            document_type=request.doc_kind or None,
            metadata={ "run_id": run.adw_id, "statement_overrides": request.statement_overrides })
        intake = facade.start( command )

        phases = [ FinancePhase( intake, phase, session_log ) for phase in intake.phases ]
        events = SssfDebugEvents( run )
        with ExitStack() as cleanup:
            monitored = [ MonitoredSssfPhase( phase, run.adw_id, phase.params.name, events )
                          for phase in phases ]
            for phase in monitored:
                cleanup.callback( phase.close )
            for phase in monitored:
                try:
                    self._phase_executor.execute( run, phase )
                except FinancePhaseFailed:
                    continue                      # later phases step aside; publish still reports

        result: IntakeRunResult = intake.state.result()
        if request.result_out:
            Path( request.result_out ).write_text( result.model_dump_json() + "\n" )
        else:
            print( result.model_dump_json())
        accepted = result.outcome not in ( Outcome.FAILED, Outcome.HALTED )
        return run.finish( accepted=accepted, reason=_summary( result ))


def _summary( result: IntakeRunResult ) -> str:
    parts = [ f"{ result.doc_kind.value }: { result.outcome.value }" ]
    if result.expense_ids:
        parts.append( "stored " + ", ".join( str( i ) for i in result.expense_ids ))
    if result.duplicate_expense_ids:
        parts.append( "matched " + ", ".join( str( i ) for i in result.duplicate_expense_ids ))
    parts.append( f"{ result.judgment_calls } judgment call(s)" )
    if result.review_id:
        parts.append( f"review { result.review_id[ :8 ]}" )
    if result.problems:
        parts.append( "; ".join( result.problems )[ :300 ])
    return " | ".join( parts )


def _live_facade( request: IntakeRequest ) -> IntakeFacade:
    from finance_app.intake.live import live_ports
    ports: IntakePorts = live_ports( conversation_id=request.conversation_id,
                                     dispatched_at=request.dispatched_at )
    return build_facade( ports )


def build_application( facade_for=_live_facade ) -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return IntakeApplication(
        argument_parser = ArgparseIntakeArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(             bootstrap_log          ),
        session_factory = SessionRunFactory(            bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(   bootstrap_log          ),
        facade_for      = facade_for,
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

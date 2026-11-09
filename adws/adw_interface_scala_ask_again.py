#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "claude-agent-sdk"]
# ///
"""ADW Interface Scala Ask Again -- re-classify the pages Haiku never actually saw.

When the Haiku call itself fails (before 2026-11-02 the SDK's 1 MiB stream cap
killed every call that read a full-size scan), Scala treats the failure as
"unsure": the page waits in `scanned_documents\\inbox`, a question goes on
the company to-do list quoting the error, and the ledger settles it. Nothing
ever asks again. This does, for every open question whose reason says the
model was never reached:

    ask_again -> report

`ask_again` reads the page again (tesseract), runs the rules and then Haiku
exactly as a fresh scan would, and either files it (sure, an allowed folder,
not finance -- the to-do is checked off and no lesson is written, because EG
taught nothing) or rewrites the question so it says what the page looks like
instead of the old error. A call that still fails leaves the question alone.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_interface_scala_ask_again.py \\
        "why" [--message-out /tmp/line.txt] [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams

from company_todos.store import JsonlTodoStore

from scala.cabinet import FilingCabinet
from scala.desk import ScalaDesk
from scala.ledger import JsonlScalaLedger
from scala.mazda_verdicts import RecentReportVerdicts
from scala.ocr import TesseractOcr
from scala.questions import JsonlLessonBook, JsonlQuestionBook, QuestionDesk
from scala.retry import QuestionRetry
from scala.rules import RuleClassifier
from scala.todo_asker import TodoListAsker

from adw_interface_scala_file_scan import _haiku as scala_haiku   # the one definition of Scala's Haiku

REQUIRED_AGENTS: list[ str ] = []   # Haiku is reached through the scala agent folder, not sssf.config


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class AskAgainArgs:
    prompt:      str
    message_out: str
    config:      str
    adw_id:      str | None


@dataclass
class AskAgainRun:
    args:  AskAgainArgs
    lines: list[ str ] = field( default_factory=list )


# -- Phases ------------------------------------------------------------------

class AskAgainPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: AskAgainRun, retry: QuestionRetry, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state
        self._retry = retry

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="ask_again", kind="code", owner="scala",
                            description="Classify again every waiting page Haiku never saw" )

    def act( self, handle: IPhaseHandle ) -> str:
        self._state.lines = self._retry.retry_all()
        handle.log( questions=len( self._state.lines ))
        return f"{ len( self._state.lines )} line(s)"


class ReportPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: AskAgainRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="scala",
                            description="Write one line per question" )

    def act( self, handle: IPhaseHandle ) -> str:
        text = "\n".join( self._state.lines )
        out = self._state.args.message_out
        if out:
            Path( out ).write_text( text + "\n" )
        else:
            print( text )
        return out or "stdout"


# -- Argument parsing --------------------------------------------------------

class ArgparseAskAgainArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> AskAgainArgs:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="ask scala again" )
        parser.add_argument( "--message-out", default="" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None )
        parsed = parser.parse_args( argv )
        return AskAgainArgs( prompt=utils.resolve_prompt( parsed.prompt ), message_out=parsed.message_out,
                             config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class AskAgainApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  retry:           QuestionRetry,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._retry           = retry

    def run( self, argv: list[ str ]) -> int:
        args = self._argument_parser.parse( argv )
        cfg = self._config_loader.load( args.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, args.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        state = AskAgainRun( args=args )
        for phase in ( AskAgainPhase( state, self._retry, session_log ),
                       ReportPhase(   state, session_log )):
            self._phase_executor.execute( run, phase )
        return run.finish( accepted=True, reason="; ".join( state.lines )[ :300 ])


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    cabinet, ledger, lessons = FilingCabinet(), JsonlScalaLedger(), JsonlLessonBook()
    desk = ScalaDesk( ocr=TesseractOcr(), mazda=RecentReportVerdicts(), rules=RuleClassifier(),
                      haiku=scala_haiku( lessons ), cabinet=cabinet, ledger=ledger )
    questions = QuestionDesk( book=JsonlQuestionBook(), lessons=lessons,
                              asker=TodoListAsker( JsonlTodoStore()), cabinet=cabinet, ledger=ledger )
    return AskAgainApplication(
        argument_parser = ArgparseAskAgainArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(                bootstrap_log          ),
        session_factory = SessionRunFactory(               bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(      bootstrap_log          ),
        retry           = QuestionRetry( desk, questions, cabinet ),
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

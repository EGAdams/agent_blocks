#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface Scala Answer -- EG tells Scala which folder an inbox scan goes in.

When Scala can't tell where a scan goes it waits in `scanned_documents\\inbox`
and becomes an item on the company to-do list ("#7 Scala needs a folder ...").
Toyota's `scala_questions` route runs this with EG's sentence and repeats the
one line it writes, with no model in the path.

    read_answer -> file_and_remember -> report

`file_and_remember` files the scan into the folder (made if new, fenced to one
level under bills_511/ or documents_511/), takes Scala's copy out of the inbox,
writes a lesson Haiku reads on every later scan, and checks the to-do off.
With no folder in the sentence it changes nothing and lists what Scala is
waiting on.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_interface_scala_answer.py \\
        "why" --request "Scala, file #7 under documents_511/insurance" \\
        [--number 7 --folder documents_511/insurance] \\
        [--message-out /tmp/line.txt] [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
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
from scala.ledger import JsonlScalaLedger
from scala.questions import AnswerRequest, JsonlLessonBook, JsonlQuestionBook, QuestionDesk, read_answer
from scala.todo_asker import TodoListAsker

REQUIRED_AGENTS: list[ str ] = []   # no model anywhere in this workflow


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class AnswerArgs:
    prompt:      str
    request:     str
    number:      int | None
    folder:      str
    message_out: str
    config:      str
    adw_id:      str | None


@dataclass
class AnswerRun:
    args:   AnswerArgs
    answer: AnswerRequest | None = None
    line:   str = ""


# -- Phases ------------------------------------------------------------------

class ReadAnswerPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: AnswerRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="read_answer", kind="code", owner="scala",
                            description="Read the question number and folder out of EG's sentence" )

    def act( self, handle: IPhaseHandle ) -> str:
        a = self._state.args
        heard = read_answer( a.request )
        self._state.answer = AnswerRequest( number=a.number if a.number is not None else heard.number,
                                            folder=a.folder or heard.folder )
        handle.log( number=self._state.answer.number, folder=self._state.answer.folder )
        return f"#{ self._state.answer.number } -> { self._state.answer.folder or '(no folder)' }"


class FileAndRememberPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: AnswerRun, questions: QuestionDesk, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state
        self._questions = questions

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="file_and_remember", kind="code", owner="scala",
                            description="File the scan, remember the folder, check the to-do off" )

    def act( self, handle: IPhaseHandle ) -> str:
        assert self._state.answer is not None
        self._state.line = self._questions.answer( self._state.answer )
        return self._state.line[ :80 ]


class ReportPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: AnswerRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="scala",
                            description="Write the one line Toyota repeats" )

    def act( self, handle: IPhaseHandle ) -> str:
        out = self._state.args.message_out
        if out:
            Path( out ).write_text( self._state.line + "\n" )
        else:
            print( self._state.line )
        return out or "stdout"


# -- Argument parsing --------------------------------------------------------

class ArgparseAnswerArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> AnswerArgs:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="answer scala" )
        parser.add_argument( "--request", default="", help="EG's sentence, unchanged" )
        parser.add_argument( "--number", type=int, default=None, help="the to-do / question number" )
        parser.add_argument( "--folder", default="", help="e.g. documents_511/insurance" )
        parser.add_argument( "--message-out", default="" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None )
        parsed = parser.parse_args( argv )
        return AnswerArgs( prompt=utils.resolve_prompt( parsed.prompt ), request=parsed.request,
                           number=parsed.number, folder=parsed.folder.strip().strip( "/" ).lower(),
                           message_out=parsed.message_out, config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class AnswerApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  questions:       QuestionDesk,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._questions       = questions

    def run( self, argv: list[ str ]) -> int:
        args = self._argument_parser.parse( argv )
        cfg = self._config_loader.load( args.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, args.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        state = AnswerRun( args=args )
        for phase in ( ReadAnswerPhase(      state, session_log ),
                       FileAndRememberPhase( state, self._questions, session_log ),
                       ReportPhase(          state, session_log )):
            self._phase_executor.execute( run, phase )
        return run.finish( accepted=True, reason=state.line )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    questions = QuestionDesk( book=JsonlQuestionBook(), lessons=JsonlLessonBook(),
                              asker=TodoListAsker( JsonlTodoStore()), cabinet=FilingCabinet(),
                              ledger=JsonlScalaLedger())
    return AnswerApplication(
        argument_parser = ArgparseAnswerArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(              bootstrap_log          ),
        session_factory = SessionRunFactory(             bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(    bootstrap_log          ),
        questions       = questions,
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

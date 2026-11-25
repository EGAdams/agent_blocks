#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface Scala Mail Replies -- EG's emailed answers reach Scala through the post office.

Scala mails each new question to EG (`scala.mail.EmailingAsker`, subject
stamped `[Scala #N]`). This run, on the `scala-mail-replies.timer`:

    check_mail -> report

`check_mail` does nothing at all while no mailed question is open. Otherwise
it asks Yolanda (through the post office) for stamped replies, posts each new
one to Scala's card (`answer`, which runs adw_interface_scala_answer.py), and
has Yolanda send Scala's one line back in the same thread. Each reply is
handled once (`~/scala/mail.jsonl`).

`--send N` mails an open question that was asked before email existed.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_interface_scala_mail_replies.py \\
        "check Scala's mail" [--send 10] [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]
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

from scala.cabinet import FilingCabinet
from scala.mail import EmailingAsker, JsonlMailBook
from scala.mail_replies import ReplyCourier
from scala.post_office_mail import PostOfficeMail
from scala.questions import JsonlQuestionBook, QuestionState

REQUIRED_AGENTS: list[ str ] = []   # the models are Yolanda's, behind the post office


@dataclass( frozen=True )
class MailArgs:
    prompt: str
    send:   int | None
    config: str
    adw_id: str | None


@dataclass
class MailRun:
    args: MailArgs
    line: str = ""


class _AlreadyNumbered:
    """IAskPerson for `--send`: the question already has its number and to-do item."""

    def __init__( self, number: int ) -> None:
        self._number = number

    def ask( self, text: str ) -> int:
        return self._number

    def close( self, number: int ) -> None:
        pass


class CheckMailPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: MailRun, book: JsonlQuestionBook, mail: PostOfficeMail,
                  cabinet: FilingCabinet, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state, self._book, self._mail, self._cabinet = state, book, mail, cabinet

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="check_mail", kind="code", owner="scala",
                            description="Mail a question, or carry EG's emailed answers to Scala" )

    def act( self, handle: IPhaseHandle ) -> str:
        questions = self._book.items()
        if self._state.args.send is not None:
            self._state.line = self._send( questions, self._state.args.send )
        else:
            report = ReplyCourier( questions, JsonlMailBook(), inbox=self._mail, post=self._mail,
                                   folders=self._cabinet.folders()).run()
            handle.log( looked_for=report.looked_for, handled=len( report.handled ))
            self._state.line = report.line()
        return self._state.line[ :80 ]

    def _send( self, questions, number: int ) -> str:
        question = next(( q for q in questions if q.number == number ), None )
        if question is None or question.state is not QuestionState.OPEN:
            return f"Scala has no open question #{ number }, so nothing was mailed."
        text = ( f"Scala needs a folder for { Path( question.scan ).name } (in scanned_documents/inbox): "
                 f"{ question.looked_like }." )
        EmailingAsker( _AlreadyNumbered( number ), self._mail, JsonlMailBook()).ask( text )
        return f"Scala's question #{ number } is on its way to EG's email."


class ReportPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: MailRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="scala", description="Say what happened" )

    def act( self, handle: IPhaseHandle ) -> str:
        print( self._state.line )
        return "stdout"


class ArgparseMailArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> MailArgs:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="check Scala's mail" )
        parser.add_argument( "--send", type=int, default=None, help="mail this open question now" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None )
        parsed = parser.parse_args( argv )
        return MailArgs( prompt=utils.resolve_prompt( parsed.prompt ), send=parsed.send,
                         config=parsed.config, adw_id=parsed.adw_id )


class MailApplication( LifecycleLogged, IApplication ):
    def __init__( self, argument_parser: IArgumentParser, config_loader: IConfigLoader,
                  session_factory: ISessionFactory, phase_executor: IPhaseExecutor,
                  book: JsonlQuestionBook, mail: PostOfficeMail, cabinet: FilingCabinet,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser, self._config_loader = argument_parser, config_loader
        self._session_factory, self._phase_executor = session_factory, phase_executor
        self._book, self._mail, self._cabinet = book, mail, cabinet

    def run( self, argv: list[ str ]) -> int:
        args = self._argument_parser.parse( argv )
        cfg = self._config_loader.load( args.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, args.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )
        state = MailRun( args=args )
        for phase in ( CheckMailPhase( state, self._book, self._mail, self._cabinet, session_log ),
                       ReportPhase( state, session_log )):
            self._phase_executor.execute( run, phase )
        return run.finish( accepted=True, reason=state.line )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    cabinet = FilingCabinet()
    return MailApplication(
        argument_parser = ArgparseMailArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(            bootstrap_log ),
        session_factory = SessionRunFactory(           bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(  bootstrap_log ),
        book            = JsonlQuestionBook(),
        mail            = PostOfficeMail( folders=cabinet.folders ),
        cabinet         = cabinet,
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

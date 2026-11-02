#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "claude-agent-sdk"]
# ///
"""ADW Interface Scala File Scan -- file one scanned page into scanned_documents.

Scala's watcher (`lancedb_memory/scala/watch.py`) runs this once per new scan
that the dashboard staged into `incoming_scans/`. Mazda goes first: anything
her intake classifier called a receipt or statement is left to her.

    check_seen -> check_mazda -> read_text -> classify -> file -> ask -> report

`read_text` is tesseract; `classify` is Scala's pattern rules, and only when
they are under 0.90 sure, Claude Haiku. `file` copies (never moves) the scan
into `C:\\Users\\NewUser\\scanned_documents\\<folder>`, creating the folder the
first time a kind of document turns up; a scan nobody is sure of waits in
`scanned_documents\\inbox`, and `ask` puts it to EG as an item on the company
to-do list. EG's answer goes through `adw_interface_scala_answer.py`, which
files it and teaches Haiku where pages like it go (`scala/questions.py`).

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_interface_scala_file_scan.py \\
        "why" --scan /path/to/scan.jpg [--folder bills_511/water] [--dry-run] \\
        [--message-out /tmp/line.txt] [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

`--folder` is a person's answer: skip classification and file it there.
`--dry-run` decides and reports but copies nothing and records nothing.
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
from scala.desk import ScalaDesk, sha256_of
from scala.haiku import DEFAULT_MODEL, HaikuClassifier
from scala.ledger import JsonlScalaLedger, summary
from scala.mazda_verdicts import RecentReportVerdicts
from scala.models import Classification, Decision, FilingOutcome, Method
from scala.ocr import TesseractOcr
from scala.questions import JsonlLessonBook, JsonlQuestionBook, QuestionDesk, lessons_for_prompt
from scala.todo_asker import TodoListAsker
from scala.rules import RuleClassifier

REQUIRED_AGENTS: list[ str ] = []   # Haiku is reached through the scala agent folder, not sssf.config


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class ScanRequest:
    prompt:      str
    scan:        Path
    folder:      str
    dry_run:     bool
    message_out: str
    config:      str
    adw_id:      str | None


@dataclass
class ScanRun:
    """What the phases hand each other. `outcome` set early means "stop here"."""
    request: ScanRequest
    sha256:  str = ""
    text:    str = ""
    verdict: Classification | None = None
    outcome: FilingOutcome | None = None
    asked:   str = ""
    notes:   list[ str ] = field( default_factory=list )


# -- Phases ------------------------------------------------------------------

class _ScalaPhase( LifecycleLogged, IPhase ):
    name = ""
    description = ""

    def __init__( self, state: ScanRun, desk: ScalaDesk, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state
        self._desk = desk

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self.name, kind="code", owner="scala", description=self.description )

    def act( self, handle: IPhaseHandle ) -> str:
        if self._state.outcome is not None:
            return "skipped"
        return self.step( handle )

    def step( self, handle: IPhaseHandle ) -> str:
        raise NotImplementedError


class CheckSeenPhase( _ScalaPhase ):
    name = "check_seen"
    description = "Refuse an unreadable scan; skip one already handled"

    def step( self, handle: IPhaseHandle ) -> str:
        scan = self._state.request.scan
        if not scan.is_file() or scan.stat().st_size == 0:
            self._state.outcome = self._desk.outcome( scan, "", Decision.ERROR,
                                                       reason="the scan is missing or empty" )
            return "missing"
        self._state.sha256 = sha256_of( scan )
        self._state.outcome = self._desk.already_handled( scan, self._state.sha256 )
        handle.log( sha256=self._state.sha256[ :12 ], duplicate=self._state.outcome is not None )
        return "duplicate" if self._state.outcome else "new"


class CheckMazdaPhase( _ScalaPhase ):
    name = "check_mazda"
    description = "Leave receipts and statements to Mazda"

    def step( self, handle: IPhaseHandle ) -> str:
        if self._state.request.folder:
            self._state.verdict = Classification( folder=self._state.request.folder, confidence=1.0,
                                                  method=Method.OPERATOR, reason="named by a person" )
            return "operator"
        claim = self._desk.mazda_claim( self._state.request.scan )
        if claim is not None:
            self._state.verdict = claim
        handle.log( mazda=claim.reason if claim else "not claimed" )
        return "mazda" if claim else "scala"


class ReadTextPhase( _ScalaPhase ):
    name = "read_text"
    description = "OCR the page with tesseract (the cheap look)"

    def step( self, handle: IPhaseHandle ) -> str:
        if self._state.verdict is not None:
            return "not needed"
        self._state.text = self._desk.read_text( self._state.request.scan )
        handle.log( chars=len( self._state.text ))
        return f"{ len( self._state.text ) } chars"


class ClassifyPhase( _ScalaPhase ):
    name = "classify"
    description = "Pattern rules; Haiku only when they are under 0.90 sure"

    def step( self, handle: IPhaseHandle ) -> str:
        if self._state.verdict is None:
            self._state.verdict = self._desk.classify( self._state.text, self._state.request.scan )
        v = self._state.verdict
        handle.log( folder=v.folder, confidence=v.confidence, method=v.method.value )
        return f"{ v.folder } ({ v.method.value } { v.confidence:.2f})"


class FilePhase( _ScalaPhase ):
    name = "file"
    description = "Copy the scan into its folder (made if new), or into the inbox"

    def step( self, handle: IPhaseHandle ) -> str:
        assert self._state.verdict is not None
        r = self._state.request
        if r.dry_run:
            v = self._state.verdict
            self._state.notes.append( f"Dry run, nothing copied: { r.scan.name } looks like { v.folder } "
                                      f"({ v.method.value } { v.confidence:.2f}): { v.reason.rstrip( '.' ) }." )
            return "dry run"
        self._state.outcome = self._desk.file( r.scan, self._state.sha256, self._state.verdict )
        handle.log( decision=self._state.outcome.decision.value, destination=self._state.outcome.destination )
        return self._state.outcome.decision.value


class AskPhase( LifecycleLogged, IPhase ):
    """Runs after `file` whatever it decided; acts only on a scan left in the inbox."""

    def __init__( self, state: ScanRun, questions: QuestionDesk, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state
        self._questions = questions

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="ask", kind="code", owner="scala",
                            description="Put an inbox scan to EG on the company to-do list" )

    def act( self, handle: IPhaseHandle ) -> str:
        outcome = self._state.outcome
        if outcome is None or outcome.decision is not Decision.INBOX:
            return "nothing to ask"
        try:
            question = self._questions.ask( outcome, self._state.text )
        except OSError as exc:                          # the scan is filed in the inbox either way
            self._state.asked = f"Scala could not add the question to the to-do list: { exc }."
            return "not asked"
        self._state.asked = f"Scala asked which folder on the company to-do list, as #{ question.number }."
        handle.log( question=question.number )
        return f"#{ question.number }"


class ReportPhase( LifecycleLogged, IPhase ):
    def __init__( self, state: ScanRun, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._state = state

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="scala",
                            description="Write the one line a person or receptionist reads" )

    def act( self, handle: IPhaseHandle ) -> str:
        line = self._state.notes[ -1 ] if self._state.outcome is None else summary( self._state.outcome )
        if self._state.asked:
            line = f"{ line } { self._state.asked }"
        out = self._state.request.message_out
        if out:
            Path( out ).write_text( line + "\n" )
        else:
            print( line )
        return out or "stdout"


# -- Argument parsing --------------------------------------------------------

class ArgparseScanArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> ScanRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="file a scan" )
        parser.add_argument( "--scan", required=True, help="the scanned image or PDF" )
        parser.add_argument( "--folder", default="", help="file it here, e.g. bills_511/water" )
        parser.add_argument( "--dry-run", action="store_true" )
        parser.add_argument( "--message-out", default="" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None )
        parsed = parser.parse_args( argv )
        return ScanRequest( prompt=utils.resolve_prompt( parsed.prompt ), scan=Path( parsed.scan ).resolve(),
                            folder=parsed.folder.strip().strip( "/" ), dry_run=parsed.dry_run,
                            message_out=parsed.message_out, config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class ScanApplication( LifecycleLogged, IApplication ):
    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  desk:            ScalaDesk,
                  questions:       QuestionDesk,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._desk            = desk
        self._questions       = questions

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        state = ScanRun( request=request )
        for phase in ( CheckSeenPhase(  state, self._desk, session_log ),
                       CheckMazdaPhase( state, self._desk, session_log ),
                       ReadTextPhase(   state, self._desk, session_log ),
                       ClassifyPhase(   state, self._desk, session_log ),
                       FilePhase(       state, self._desk, session_log ),
                       AskPhase(        state, self._questions, session_log ),
                       ReportPhase(     state, session_log )):
            self._phase_executor.execute( run, phase )
        if state.outcome is None:                       # a dry run: decided, nothing done
            return run.finish( accepted=True, reason=state.notes[ -1 ])
        if not request.dry_run:
            self._desk.record( state.outcome )          # every real run leaves exactly one ledger line
        accepted = state.outcome.decision is not Decision.ERROR
        return run.finish( accepted=accepted, reason=summary( state.outcome ))


def _haiku( lessons: JsonlLessonBook ) -> HaikuClassifier:
    """Haiku's brief is the `scala` agent folder -- the one definition of the agent."""
    from receptionist_team.agents import catalog
    definition = catalog().definition( "scala" )
    config = definition.model_configuration()
    return HaikuClassifier( system_prompt=definition.system_prompt().raw(),
                            render_task=definition.user_prompt().render,
                            model=config.model or DEFAULT_MODEL,
                            max_turns=config.max_turns,
                            lessons=lambda: lessons_for_prompt( lessons.all()))


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    cabinet, ledger, lessons = FilingCabinet(), JsonlScalaLedger(), JsonlLessonBook()
    desk = ScalaDesk( ocr=TesseractOcr(), mazda=RecentReportVerdicts(), rules=RuleClassifier(),
                      haiku=_haiku( lessons ), cabinet=cabinet, ledger=ledger )
    questions = QuestionDesk( book=JsonlQuestionBook(), lessons=lessons,
                              asker=TodoListAsker( JsonlTodoStore()), cabinet=cabinet, ledger=ledger )
    return ScanApplication(
        argument_parser = ArgparseScanArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(              bootstrap_log          ),
        session_factory = SessionRunFactory(             bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(    bootstrap_log          ),
        desk            = desk,
        questions       = questions,
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

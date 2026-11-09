"""Offline tests for Scala, the scanner clerk. Run from lancedb_memory/:

    .venv/bin/python -m scala.tests.test_scala

No tesseract, no network, no model: OCR, Mazda and Haiku are fakes, except
the ADW test, which files by --folder so nothing has to be read or asked.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from scala.cabinet import FilingCabinet
from scala.desk import ScalaDesk
from scala.haiku import HaikuClassifier, parse_reply
from scala.ledger import InMemoryScalaLedger, JsonlScalaLedger, summary
from scala.mazda_verdicts import RecentReportVerdicts
from scala.questions import ( AnswerRequest, InMemoryLessonBook, InMemoryQuestionBook, JsonlQuestionBook,
                              QuestionDesk, QuestionState, lessons_for_prompt, read_answer )
from scala.models import FINANCE, NOT_ASKED, UNSURE, Classification, Decision, Method
from scala.retry import QuestionRetry
from scala.rules import RuleClassifier
from scala.watch import ScanWatcher, Watermark

ADW = Path( __file__ ).resolve().parents[ 3 ] / "adws" / "adw_interface_scala_file_scan.py"
ANSWER_ADW = ADW.with_name( "adw_interface_scala_answer.py" )

ELECTRIC = """Consumers Energy  Account summary  Service address 100 Example St
Electric service 742 kWh used this period  Electric charges $112.40  Amount due"""
COMBINED = """Consumers Energy  Electric service 620 kWh  Natural gas 48 therms  Amount due"""
PROBATE = """STATE OF MICHIGAN  PROBATE COURT  COUNTY OF KENT  Estate of John Q. Example
File No. 25-217344-DE  Notice to the personal representative"""
RECEIPT = """MEIJER  Subtotal 14.20  Sales tax 0.85  Total 15.05  Change due 4.95  Thank you for shopping"""
WATER_THIN = """City water bill  usage 2,400 gallons  amount due"""


class FakeOcr:
    def __init__( self, text: str ) -> None:
        self.text = text

    def read( self, image: Path ) -> str:
        return self.text


class FakeMazda:
    def __init__( self, kind: str | None = None ) -> None:
        self.kind = kind

    def doc_kind( self, image: Path ) -> str | None:
        return self.kind

    def left_for_scala( self, image: Path ) -> bool:
        return self.kind == "left_for_scala"


class FakeHaiku:
    def __init__( self, answer: Classification ) -> None:
        self.answer = answer
        self.calls = 0

    def classify( self, text: str, image: Path, folders: tuple[ str, ... ]) -> Classification:
        self.calls += 1
        return self.answer


def haiku_says( folder: str, confidence: float ) -> FakeHaiku:
    return FakeHaiku( Classification( folder=folder, confidence=confidence, method=Method.HAIKU,
                                      reason="looks like it" ))


def make_tree( root: Path ) -> None:
    for folder in ( "bills_511/electric", "bills_511/gas", "bills_511/mortgage", "bills_511/xfinity",
                    "documents_511", "rol_receipts" ):
        ( root / folder ).mkdir( parents=True )


def make_scan( root: Path, content: bytes = b"\xff\xd8 fake jpeg" ) -> Path:
    scan = root / "incoming" / "window_scan_1_abc.jpg"
    scan.parent.mkdir( parents=True, exist_ok=True )
    scan.write_bytes( content )
    return scan


def desk_for( tmp: Path, text: str, mazda: str | None = None, haiku: FakeHaiku | None = None ):
    make_tree( tmp / "cabinet" )
    ledger = InMemoryScalaLedger()
    haiku = haiku or haiku_says( UNSURE, 0.0 )
    desk = ScalaDesk( FakeOcr( text ), FakeMazda( mazda ), RuleClassifier(), haiku,
                      FilingCabinet( tmp / "cabinet" ), ledger )
    return desk, ledger, haiku


async def test_rules_are_sure_of_a_plain_electric_bill_and_a_probate_letter():
    rules = RuleClassifier()
    electric = rules.classify( ELECTRIC, Path( "x.jpg" ))
    assert electric.folder == "bills_511/electric" and electric.confidence >= 0.90, electric
    probate = rules.classify( PROBATE, Path( "x.jpg" ))
    assert probate.folder == "documents_511/probate" and probate.confidence >= 0.90, probate
    assert rules.classify( RECEIPT, Path( "x.jpg" )).folder == FINANCE


async def test_rules_hand_off_when_two_kinds_fit_or_the_page_is_thin():
    rules = RuleClassifier()
    combined = rules.classify( COMBINED, Path( "x.jpg" ))
    assert combined.confidence < 0.90 and "also looks like" in combined.reason, combined
    assert rules.classify( WATER_THIN, Path( "x.jpg" )).confidence < 0.90
    assert rules.classify( "", Path( "x.jpg" )).folder == UNSURE


async def test_a_sure_rule_files_without_asking_haiku():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk, ledger, haiku = desk_for( tmp, ELECTRIC )
        outcome = desk.handle( make_scan( tmp ))
        assert outcome.decision is Decision.FILED and outcome.method is Method.RULES
        assert haiku.calls == 0
        assert Path( outcome.destination ).parent == tmp / "cabinet" / "bills_511" / "electric"
        assert Path( outcome.destination ).read_bytes() == make_scan( tmp ).read_bytes()
        assert len( ledger.outcomes ) == 1


async def test_haiku_creates_the_water_folder_the_first_time():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk, _, haiku = desk_for( tmp, WATER_THIN, haiku=haiku_says( "bills_511/water", 0.93 ))
        outcome = desk.handle( make_scan( tmp ))
        assert haiku.calls == 1
        assert outcome.decision is Decision.FILED and outcome.created_folder
        assert ( tmp / "cabinet" / "bills_511" / "water" ).is_dir()
        assert "(new folder)" in summary( outcome )


async def test_mazda_goes_first_and_finance_is_never_filed():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk, _, haiku = desk_for( tmp, ELECTRIC, mazda="receipt" )
        scan = make_scan( tmp )
        outcome = desk.handle( scan )
        assert outcome.decision is Decision.MAZDA and outcome.method is Method.MAZDA
        assert haiku.calls == 0
        assert scan.exists(), "Mazda's staged copy must never be moved"
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk, _, _ = desk_for( tmp, WATER_THIN, mazda="unknown", haiku=haiku_says( "finance", 0.9 ))
        assert desk.handle( make_scan( tmp )).decision is Decision.MAZDA
        assert not any(( tmp / "cabinet" / "rol_receipts" ).iterdir())


async def test_finance_that_mazda_was_skipped_on_waits_in_the_inbox():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk, _, _ = desk_for( tmp, WATER_THIN, mazda="left_for_scala", haiku=haiku_says( "finance", 0.9 ))
        outcome = desk.handle( make_scan( tmp ))
        assert outcome.decision is Decision.INBOX and "skipped" in outcome.reason
        assert not any(( tmp / "cabinet" / "rol_receipts" ).iterdir())


async def test_unsure_low_confidence_or_fenced_folders_wait_in_the_inbox():
    for answer in ( haiku_says( UNSURE, 0.0 ), haiku_says( "bills_511/water", 0.4 ),
                    haiku_says( "../etc", 0.99 ), haiku_says( "rol_receipts/misc", 0.99 ),
                    haiku_says( "bills_511/water/2026", 0.99 )):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path( raw )
            desk, _, _ = desk_for( tmp, WATER_THIN, haiku=answer )
            outcome = desk.handle( make_scan( tmp ))
            assert outcome.decision is Decision.INBOX, ( answer.answer, outcome )
            assert Path( outcome.destination ).parent == tmp / "cabinet" / "inbox"
            assert not ( tmp / "etc" ).exists()


async def test_the_same_image_is_filed_once():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk, ledger, _ = desk_for( tmp, ELECTRIC )
        first = desk.handle( make_scan( tmp ))
        again = desk.handle( make_scan( tmp ))
        assert first.decision is Decision.FILED and again.decision is Decision.DUPLICATE
        assert len( list(( tmp / "cabinet" / "bills_511" / "electric" ).iterdir())) == 1


async def test_haiku_replies_are_read_strictly():
    good = parse_reply( 'Looking at it.\n{"folder": "Bills_511/Water/", "confidence": 0.91, "reason": "city water bill"}' )
    assert good.folder == "bills_511/water" and good.confidence == 0.91
    assert parse_reply( "it is a water bill" ).folder == UNSURE
    assert parse_reply( '{"folder": "receipt", "confidence": 0.8}' ).folder == FINANCE
    assert parse_reply( '{"folder": "x", "confidence": "high"}' ).folder == UNSURE


async def test_mazda_verdict_is_read_from_the_dashboard_record():
    with tempfile.TemporaryDirectory() as raw:
        record = Path( raw ) / "recent_report.json"
        record.write_text( json.dumps({ "scanner_intakes": {
            "Window Scanner": { "image_path": "/s/a.jpg", "doc_kind": "receipt" },
            "Freezer Scanner": { "image_path": "/s/b.jpg", "facade": { "doc_kind": "unknown" }}}}))
        verdicts = RecentReportVerdicts( record )
        assert verdicts.doc_kind( Path( "/s/a.jpg" )) == "receipt"
        assert verdicts.doc_kind( Path( "/s/b.jpg" )) == "unknown"
        assert verdicts.doc_kind( Path( "/s/c.jpg" )) is None
        assert RecentReportVerdicts( Path( raw ) / "missing.json" ).doc_kind( Path( "/s/a.jpg" )) is None
        assert not verdicts.left_for_scala( Path( "/s/a.jpg" ))
        record.write_text( json.dumps({ "scanner_intakes": {
            "Window Scanner": { "image_path": "/s/a.jpg", "status": "complete",
                                "status_detail": "Left for Scala: not a finance document" }}}))
        assert verdicts.left_for_scala( Path( "/s/a.jpg" ))
        assert not verdicts.left_for_scala( Path( "/s/b.jpg" ))


async def test_watcher_ignores_the_backlog_and_waits_for_a_scan_to_settle():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        watch = tmp / "incoming"
        watch.mkdir()
        old = watch / "old.jpg"
        old.write_bytes( b"x" )
        os.utime( old, ( 1000, 1000 ))
        now = [ 2000.0 ]
        seen: list[ Path ] = []
        watcher = ScanWatcher( watch, Watermark( tmp / "home" ), seen.append, settle_sec=20,
                               clock=lambda: now[ 0 ])
        assert watcher.tick() == []                     # first start: watermark = now
        fresh = watch / "new.jpg"
        fresh.write_bytes( b"y" )
        os.utime( fresh, ( 2010, 2010 ))
        now[ 0 ] = 2015.0
        assert watcher.tick() == []                     # still settling
        now[ 0 ] = 2031.0
        assert watcher.tick() == [ fresh ] and seen == [ fresh ]
        assert watcher.tick() == []                     # never twice


async def test_ledger_file_is_private():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        desk = ScalaDesk( FakeOcr( ELECTRIC ), FakeMazda(), RuleClassifier(), haiku_says( UNSURE, 0 ),
                          FilingCabinet( tmp / "cabinet" ), JsonlScalaLedger( tmp / "home" ))
        make_tree( tmp / "cabinet" )
        desk.handle( make_scan( tmp ))
        mode = stat.S_IMODE(( tmp / "home" / "ledger.jsonl" ).stat().st_mode )
        assert mode == 0o600, oct( mode )


async def test_adw_files_by_a_persons_folder_and_writes_one_line():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        make_tree( tmp / "cabinet" )
        scan = make_scan( tmp )
        line_file = tmp / "line.txt"
        env = { **os.environ, "SCALA_CABINET": str( tmp / "cabinet" ), "SCALA_HOME": str( tmp / "home" ),
                "SCALA_MAZDA_RECORD": str( tmp / "none.json" ), "AGENT_POST_OFFICE_ROOT": raw }
        done = subprocess.run(
            [ sys.executable, str( ADW ), "test", "--scan", str( scan ), "--folder", "bills_511/water",
              "--message-out", str( line_file )],
            cwd=ADW.parents[ 1 ], env=env, capture_output=True, text=True, timeout=120 )
        assert done.returncode == 0, done.stderr[ -800: ] + done.stdout[ -800: ]
        line = line_file.read_text().strip()
        assert line.startswith( "Scala filed window_scan_1_abc.jpg under bills_511/water (new folder)" ), line
        assert len(( tmp / "home" / "ledger.jsonl" ).read_text().splitlines()) == 1


class FakeAsker:
    def __init__( self ) -> None:
        self.asked: list[ str ] = []
        self.closed: list[ int ] = []

    def ask( self, text: str ) -> int:
        self.asked.append( text )
        return len( self.asked ) + 6                    # to-do numbers don't start at 1

    def close( self, number: int ) -> None:
        self.closed.append( number )


def inbox_question( tmp: Path, text: str = WATER_THIN ):
    """A scan Haiku couldn't place, filed to the inbox and asked about."""
    desk, ledger, _ = desk_for( tmp, text, haiku=haiku_says( UNSURE, 0.2 ))
    outcome = desk.handle( make_scan( tmp ))
    assert outcome.decision is Decision.INBOX
    lessons, asker = InMemoryLessonBook(), FakeAsker()
    questions = QuestionDesk( InMemoryQuestionBook(), lessons, asker, FilingCabinet( tmp / "cabinet" ), ledger )
    return questions, questions.ask( outcome, text ), lessons, asker, ledger


async def test_an_inbox_scan_is_asked_on_the_to_do_list():
    with tempfile.TemporaryDirectory() as raw:
        questions, question, _, asker, _ = inbox_question( Path( raw ))
        assert question.number == 7 and question.state is QuestionState.OPEN
        assert "Scala needs a folder" in asker.asked[ 0 ] and "inbox" in asker.asked[ 0 ]
        assert "water" in question.hints and "gallons" in question.hints
        assert [ q.number for q in questions.open_questions()] == [ 7 ]


async def test_an_answer_files_it_makes_the_folder_remembers_and_checks_off():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        questions, question, lessons, asker, ledger = inbox_question( tmp )
        line = questions.answer( read_answer( "Scala, file #7 under bills_511/water" ))
        assert line.startswith( "Scala filed question #7 under bills_511/water (new folder)" ), line
        assert len( list(( tmp / "cabinet" / "bills_511" / "water" ).iterdir())) == 1
        assert not Path( question.scan ).exists(), "out of the inbox"
        assert ( tmp / "incoming" / "window_scan_1_abc.jpg" ).exists(), "Mazda's original is never touched"
        assert asker.closed == [ 7 ] and questions.open_questions() == []
        assert lessons.lessons[ 0 ].folder == "bills_511/water"
        assert "bills_511/water" in lessons_for_prompt( lessons.all()) and "gallons" in lessons_for_prompt( lessons.all())
        assert ledger.outcomes[ -1 ].method is Method.OPERATOR
        again = questions.answer( AnswerRequest( number=7, folder="bills_511/gas" ))
        assert "already answered" in again


async def test_an_unclear_or_fenced_answer_files_nothing():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        questions, question, lessons, asker, _ = inbox_question( tmp )
        assert "waiting on 1: #7" in questions.answer( read_answer( "what is Scala asking about?" ))
        assert "can't make" in questions.answer( AnswerRequest( number=7, folder="rol_receipts/misc" ))
        assert "never asked a question #3" in questions.answer( AnswerRequest( number=3, folder="bills_511/water" ))
        assert Path( question.scan ).exists() and asker.closed == [] and lessons.lessons == []


async def test_answers_are_read_from_plain_sentences():
    assert read_answer( "Scala, put #12 in documents_511/Insurance." ) == AnswerRequest( number=12, folder="documents_511/insurance" )
    assert read_answer( "file question 4 in a new bills folder called property tax" ) == \
        AnswerRequest( number=4, folder="bills_511/property_tax" )
    assert read_answer( "Scala, make a documents folder named church letters" ).folder == "documents_511/church_letters"
    assert read_answer( "what's Scala waiting on" ) == AnswerRequest()


async def test_question_book_is_private_and_keeps_the_latest_version():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        _, question, _, _, _ = inbox_question( tmp )
        book = JsonlQuestionBook( tmp / "home" )
        book.put( question )
        book.put( question.model_copy( update={ "state": QuestionState.ANSWERED, "folder": "bills_511/water" }))
        assert [ q.state for q in book.items()] == [ QuestionState.ANSWERED ]
        assert stat.S_IMODE(( tmp / "home" / "questions.jsonl" ).stat().st_mode ) == 0o600


async def test_answer_adw_files_and_checks_off_the_to_do():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        make_tree( tmp / "cabinet" )
        from company_todos.store import JsonlTodoStore
        from scala.todo_asker import TodoListAsker
        ledger = JsonlScalaLedger( tmp / "home" )
        desk = ScalaDesk( FakeOcr( WATER_THIN ), FakeMazda(), RuleClassifier(), haiku_says( UNSURE, 0.1 ),
                          FilingCabinet( tmp / "cabinet" ), ledger )
        outcome = desk.handle( make_scan( tmp ))
        store = JsonlTodoStore( tmp / "todos" )
        QuestionDesk( JsonlQuestionBook( tmp / "home" ), InMemoryLessonBook(), TodoListAsker( store ),
                      FilingCabinet( tmp / "cabinet" ), ledger ).ask( outcome, WATER_THIN )
        line_file = tmp / "line.txt"
        env = { **os.environ, "SCALA_CABINET": str( tmp / "cabinet" ), "SCALA_HOME": str( tmp / "home" ),
                "COMPANY_TODOS_HOME": str( tmp / "todos" ), "AGENT_POST_OFFICE_ROOT": raw }
        done = subprocess.run(
            [ sys.executable, str( ANSWER_ADW ), "test", "--request", "Scala, file #1 under bills_511/water",
              "--message-out", str( line_file )],
            cwd=ANSWER_ADW.parents[ 1 ], env=env, capture_output=True, text=True, timeout=120 )
        assert done.returncode == 0, done.stderr[ -800: ] + done.stdout[ -800: ]
        assert line_file.read_text().startswith( "Scala filed question #1 under bills_511/water (new folder)" )
        assert store.items()[ 0 ].state.value == "done" and store.items()[ 0 ].added_by == "scala"
        assert ( tmp / "home" / "lessons.jsonl" ).exists()


# -- asking again when Haiku was never reached --------------------------------

def failed_ask_question( tmp: Path ):
    """A scan whose Haiku call died: inbox, asked about, quoting the error."""
    failed = FakeHaiku( Classification( folder=UNSURE, confidence=0.0, method=Method.HAIKU, answered=False,
                                        reason=f"{ NOT_ASKED }: CLIJSONDecodeError: message over 1 MiB" ))
    desk, ledger, _ = desk_for( tmp, WATER_THIN, haiku=failed )
    outcome = desk.handle( make_scan( tmp ))
    assert outcome.decision is Decision.INBOX
    lessons, asker = InMemoryLessonBook(), FakeAsker()
    cabinet = FilingCabinet( tmp / "cabinet" )
    questions = QuestionDesk( InMemoryQuestionBook(), lessons, asker, cabinet, ledger )
    question = questions.ask( outcome, WATER_THIN )
    retry = QuestionRetry( desk, questions, cabinet )
    return retry, questions, question, failed, lessons, asker, ledger


async def test_a_failed_haiku_call_says_so_in_its_classification():
    def render( fields: dict[ str, str ]) -> str:
        return "task"
    classifier = HaikuClassifier( "system", render, timeout_sec=0.0 )
    verdict = classifier.classify( "text", Path( "missing.jpg" ), ())
    assert verdict.answered is False and verdict.reason.startswith( NOT_ASKED ), verdict
    assert parse_reply( '{"folder": "bills_511/gas", "confidence": 0.9}' ).answered is True


async def test_asked_again_and_sure_it_is_filed_and_checked_off_without_a_lesson():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        retry, questions, question, haiku, lessons, asker, ledger = failed_ask_question( tmp )
        assert questions.unclassified() == [ question ]
        haiku.answer = Classification( folder="bills_511/water", confidence=0.85, method=Method.HAIKU,
                                       reason="a city water bill" )
        lines = retry.retry_all()
        assert lines[ 0 ].startswith( "Scala filed question #7 under bills_511/water (new folder) on her own" ), lines
        assert len( list(( tmp / "cabinet" / "bills_511" / "water" ).iterdir())) == 1
        assert not Path( question.scan ).exists() and asker.closed == [ 7 ]
        assert lessons.lessons == [], "EG taught nothing, so nothing is remembered as his"
        assert ledger.outcomes[ -1 ].decision is Decision.FILED and ledger.outcomes[ -1 ].method is Method.HAIKU
        assert questions.open_questions() == [] and retry.retry_all()[ 0 ].startswith( "Scala has no questions" )


async def test_asked_again_and_unsure_it_waits_with_what_it_looks_like_now():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        retry, questions, question, haiku, lessons, asker, _ = failed_ask_question( tmp )
        haiku.answer = Classification( folder="documents_511/probate", confidence=0.5, method=Method.HAIKU,
                                       reason="a death certificate" )
        line = retry.retry_all()[ 0 ]
        assert line.startswith( "#7 still needs EG: a death certificate; best guess documents_511/probate" ), line
        waiting = questions.open_questions()
        assert len( waiting ) == 1 and NOT_ASKED not in waiting[ 0 ].looked_like
        assert questions.unclassified() == [] and Path( question.scan ).exists() and asker.closed == []


async def test_asked_again_and_still_unreachable_nothing_changes():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        retry, questions, question, haiku, _, asker, _ = failed_ask_question( tmp )
        line = retry.retry_all()[ 0 ]
        assert "still could not be asked" in line and haiku.calls == 2
        assert questions.unclassified() == [ question ] and asker.closed == []


async def test_asked_again_as_finance_it_is_not_filed_by_scala():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path( raw )
        retry, questions, question, haiku, _, _, _ = failed_ask_question( tmp )
        haiku.answer = Classification( folder=FINANCE, confidence=0.95, method=Method.HAIKU, reason="a receipt" )
        assert "looks financial" in retry.retry_all()[ 0 ]
        assert Path( question.scan ).exists() and len( questions.open_questions()) == 1


if __name__ == "__main__":
    from tests.runner import main
    main( globals())

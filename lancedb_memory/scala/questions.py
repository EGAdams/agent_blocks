"""Scala's questions: when nobody can tell where a scan goes, ask EG, then remember.

A scan that lands in `scanned_documents/inbox` becomes one item on the company
to-do list ("#7 Scala needs a folder for ..."); the to-do number is the
question's number, so there is one numbering to say out loud. EG answers
through Toyota -- "Scala, file #7 under documents_511/insurance" -- and
`QuestionDesk.answer`:

1. files the inbox copy into that folder (made if new, same fence as ever),
2. takes it out of the inbox (the inbox copy is Scala's own; Mazda's staged
   original in incoming_scans is never touched),
3. writes a Lesson -- the folder, what the page looked like, its distinctive
   words -- which Haiku is shown on every later scan, and
4. checks the to-do item off.

Questions and lessons are append-only JSONL beside the ledger in `~/scala/`
(0700 / 0600): they hold OCR words from personal paperwork.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .interfaces import IFilingCabinet, IScalaLedger
from .ledger import default_home
from .models import NOT_ASKED, UNSURE, Classification, Decision, FilingOutcome, Method


class QuestionState( str, Enum ):
    OPEN     = "open"
    ANSWERED = "answered"


class Question( BaseModel ):
    """One scan Scala asked about. The book keeps every version of it."""

    model_config = ConfigDict( extra="forbid" )

    number:      int                     # the company to-do item's number
    asked_at:    datetime
    scan:        str                     # Scala's copy in scanned_documents/inbox
    source:      str                     # Mazda's staged original, never touched
    sha256:      str
    looked_like: str = ""                # what the rules or Haiku made of it
    hints:       tuple[ str, ... ] = ()  # the page's distinctive words
    state:       QuestionState = QuestionState.OPEN
    folder:      str = ""
    answered_at: datetime | None = None


class Lesson( BaseModel ):
    """Where EG said a kind of page goes. Haiku reads these on every scan."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    folder:      str
    looked_like: str = ""
    hints:       tuple[ str, ... ] = ()
    taught_at:   datetime


class AnswerRequest( BaseModel ):
    """What one sentence to Toyota asked of Scala, read without a model."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    number: int | None = None
    folder: str = ""


class IAskPerson( Protocol ):
    """Where Scala's questions are put to a person."""

    def ask( self, text: str ) -> int:
        """Put the question somewhere it will be seen; return its number."""
        ...

    def close( self, number: int ) -> None: ...


# -- reading a sentence ------------------------------------------------------

_PATH   = re.compile( r"\b(bills_511|documents_511)\s*/\s*([a-z0-9][a-z0-9_]{1,39})\b", re.I )
_NAMED  = re.compile( r"\b(bills?|documents?|letters?)\s+folder\s+(?:called|named)\s+[\"']?([a-z][a-z0-9 _-]{1,38}?)[\"']?\s*(?:[.,!?]|$)", re.I )
_NUMBER = re.compile( r"#\s*(\d+)|\b(?:question|number|item|to[\s-]?do|no\.?)\s*#?\s*(\d+)\b", re.I )


def read_answer( sentence: str ) -> AnswerRequest:
    """`#7` / `question 7`, and `bills_511/water` / `a bills folder called water`."""
    number = None
    found = _NUMBER.search( sentence )
    if found:
        number = int( found.group( 1 ) or found.group( 2 ))
    folder = ""
    path = _PATH.search( sentence )
    if path:
        folder = f"{ path.group( 1 ).lower() }/{ path.group( 2 ).lower() }"
    else:
        named = _NAMED.search( sentence )
        if named:
            top = "bills_511" if named.group( 1 ).lower().startswith( "bill" ) else "documents_511"
            leaf = re.sub( r"[^a-z0-9]+", "_", named.group( 2 ).lower()).strip( "_" )
            folder = f"{ top }/{ leaf }" if leaf else ""
    return AnswerRequest( number=number, folder=folder )


_STOP = frozenset( """
    the and for with from this that your you our are was were have has will
    please page date amount total account number service address phone thank
    thanks payment pay due balance customer statement bill notice dear sincerely
    """.split())


def hints_from( text: str, limit: int = 12 ) -> tuple[ str, ... ]:
    """The page's most frequent ordinary words -- no digits, nothing short."""
    words = [ w for w in re.findall( r"[a-z]{4,}", text.lower()) if w not in _STOP ]
    return tuple( w for w, _ in Counter( words ).most_common( limit ))


# -- the books ---------------------------------------------------------------

def _append( home: Path, path: Path, line: str ) -> None:
    home.mkdir( parents=True, exist_ok=True )
    os.chmod( home, 0o700 )
    fd = os.open( path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600 )
    with os.fdopen( fd, "a", encoding="utf-8" ) as handle:
        handle.write( line + "\n" )
        handle.flush()
        os.fsync( handle.fileno())


def _lines( path: Path ) -> list[ str ]:
    if not path.exists():
        return []
    return [ line for line in path.read_text( encoding="utf-8" ).splitlines() if line.strip()]


class JsonlQuestionBook:
    """`~/scala/questions.jsonl`; the current question is the last line per number."""

    def __init__( self, home: Path | None = None ) -> None:
        self._home = home or default_home()
        self._path = self._home / "questions.jsonl"

    def put( self, question: Question ) -> None:
        _append( self._home, self._path, question.model_dump_json())

    def items( self ) -> list[ Question ]:
        current: dict[ int, Question ] = {}
        for line in _lines( self._path ):
            try:
                question = Question.model_validate_json( line )
            except ValueError:
                continue
            current[ question.number ] = question
        return [ current[ n ] for n in sorted( current )]


class JsonlLessonBook:
    """`~/scala/lessons.jsonl`: every folder EG has named for a page Scala asked about."""

    def __init__( self, home: Path | None = None ) -> None:
        self._home = home or default_home()
        self._path = self._home / "lessons.jsonl"

    def add( self, lesson: Lesson ) -> None:
        _append( self._home, self._path, lesson.model_dump_json())

    def all( self ) -> list[ Lesson ]:
        lessons: list[ Lesson ] = []
        for line in _lines( self._path ):
            try:
                lessons.append( Lesson.model_validate_json( line ))
            except ValueError:
                continue
        return lessons


class InMemoryQuestionBook:
    def __init__( self ) -> None:
        self.versions: list[ Question ] = []

    def put( self, question: Question ) -> None:
        self.versions.append( question )

    def items( self ) -> list[ Question ]:
        current = { q.number: q for q in self.versions }
        return [ current[ n ] for n in sorted( current )]


class InMemoryLessonBook:
    def __init__( self ) -> None:
        self.lessons: list[ Lesson ] = []

    def add( self, lesson: Lesson ) -> None:
        self.lessons.append( lesson )

    def all( self ) -> list[ Lesson ]:
        return list( self.lessons )


def lessons_for_prompt( lessons: list[ Lesson ], limit: int = 20 ) -> str:
    """The lines Haiku reads under "Where EG has filed pages Scala asked about"."""
    lines = []
    for lesson in lessons[ -limit: ]:
        words = ", ".join( lesson.hints[ :8 ])
        what = lesson.looked_like or "a page"
        lines.append( f"- { lesson.folder }: { what }" + ( f" (words like { words })" if words else "" ))
    return "\n".join( lines ) or "- (none yet)"


# -- asking and answering ----------------------------------------------------

class QuestionDesk:
    def __init__( self,
                  book:    JsonlQuestionBook | InMemoryQuestionBook,
                  lessons: JsonlLessonBook | InMemoryLessonBook,
                  asker:   IAskPerson,
                  cabinet: IFilingCabinet,
                  ledger:  IScalaLedger ) -> None:
        self._book = book
        self._lessons = lessons
        self._asker = asker
        self._cabinet = cabinet
        self._ledger = ledger

    def open_questions( self ) -> list[ Question ]:
        return [ q for q in self._book.items() if q.state is QuestionState.OPEN ]

    def ask( self, outcome: FilingOutcome, text: str ) -> Question:
        """Put one inbox scan to EG. Returns the question, already numbered."""
        guess = ""
        if outcome.folder not in ( "", UNSURE ):
            guess = f"best guess { outcome.folder }"
        looked_like = "; ".join( part for part in ( outcome.reason.rstrip( "." )[ :160 ], guess ) if part )
        name = Path( outcome.destination ).name
        number = self._asker.ask(
            f"Scala needs a folder for { name } (in scanned_documents/inbox)"
            + ( f": { looked_like }" if looked_like else "" )
            + ". Tell Toyota: \"Scala, file #<this number> under bills_511/<kind> or documents_511/<kind>\"." )
        question = Question( number=number, asked_at=datetime.now().astimezone(), scan=outcome.destination,
                             source=outcome.source, sha256=outcome.sha256, looked_like=looked_like,
                             hints=hints_from( text ))
        self._book.put( question )
        return question

    def answer( self, request: AnswerRequest ) -> str:
        """File one asked-about scan where EG said, remember it, check the to-do off. One line back."""
        waiting = self.open_questions()
        if not request.folder and request.number is None:
            return self._waiting_line( waiting, "" )
        if not request.folder:
            return self._waiting_line( waiting, "I didn't hear a folder, so Scala filed nothing." )
        if not self._cabinet.is_allowed( request.folder ):
            return ( f"Scala can't make { request.folder }: new folders go one level under bills_511/ or "
                     f"documents_511/, in lower-case words like documents_511/insurance. Nothing was filed." )
        if request.number is None:
            if len( waiting ) != 1:
                return self._waiting_line( waiting, "Which question? Scala filed nothing." )
            question = waiting[ 0 ]
        else:
            question = next(( q for q in self._book.items() if q.number == request.number ), None )
            if question is None:
                return self._waiting_line( waiting, f"Scala never asked a question #{ request.number }, so nothing was filed." )
            if question.state is QuestionState.ANSWERED:
                return f"Scala's question #{ question.number } was already answered: it went under { question.folder }."
        return self._file( question, request.folder )

    # -- asking again, when the first ask never reached the model ---------------

    def unclassified( self ) -> list[ Question ]:
        """Open questions whose page Haiku never actually saw (the call itself failed)."""
        return [ q for q in self.open_questions() if q.looked_like.startswith( NOT_ASKED )]

    def image_of( self, question: Question ) -> Path | None:
        """The page to read: Scala's inbox copy, else Mazda's staged original."""
        for candidate in ( Path( question.scan ), Path( question.source )):
            if candidate.is_file():
                return candidate
        return None

    def file_as_classified( self, question: Question, verdict: Classification ) -> str:
        """Haiku placed it on a second ask: file it, check the to-do off, teach nothing (EG didn't say)."""
        return self._file( question, verdict.folder, method=verdict.method, confidence=verdict.confidence,
                           reason=f"asked again for question #{ question.number }: { verdict.reason }"[ :300 ],
                           taught=False )

    def revise( self, question: Question, looked_like: str ) -> Question:
        """Replace what the page looked like, so the waiting list stops quoting a stale error."""
        revised = question.model_copy( update={ "looked_like": looked_like })
        self._book.put( revised )
        return revised

    def _file( self, question: Question, folder: str, method: Method = Method.OPERATOR,
               confidence: float = 1.0, reason: str = "", taught: bool = True ) -> str:
        inbox_copy = Path( question.scan )
        image = self.image_of( question )
        if image is None:
            return f"Scala can't find the scan for question #{ question.number } any more ({ inbox_copy.name }), so nothing was filed."
        try:
            destination, created = self._cabinet.file( image, folder, question.sha256 )
        except OSError as exc:
            return f"Scala could not file question #{ question.number }: { exc }."
        if inbox_copy.is_file() and destination.is_file() and inbox_copy != destination:
            inbox_copy.unlink()
        now = datetime.now().astimezone()
        self._ledger.record( FilingOutcome(
            at=now, source=question.source, sha256=question.sha256, decision=Decision.FILED, folder=folder,
            destination=str( destination ), method=method, confidence=confidence,
            reason=reason or f"EG answered question #{ question.number }", created_folder=created ))
        self._book.put( question.model_copy( update={ "state": QuestionState.ANSWERED, "folder": folder,
                                                      "answered_at": now }))
        self._asker.close( question.number )
        new = " (new folder)" if created else ""
        if not taught:
            return ( f"Scala filed question #{ question.number } under { folder }{ new } on her own "
                     f"({ method.value }, { confidence:.2f}) and checked it off the to-do list." )
        if question.hints:                      # a page with no words read teaches Haiku nothing
            self._lessons.add( Lesson( folder=folder, looked_like=question.looked_like, hints=question.hints,
                                       taught_at=now ))
        remember = ( " She'll remember that pages like it go there." if question.hints
                     else " The page had no words she could read, so there's nothing to remember from it." )
        return ( f"Scala filed question #{ question.number } under { folder }{ new } and checked it off the "
                 f"to-do list.{ remember }" )

    def _waiting_line( self, waiting: list[ Question ], lead: str ) -> str:
        lead = f"{ lead } " if lead else ""
        if not waiting:
            return f"{ lead }Scala isn't waiting on any questions."
        listed = "; ".join( f"#{ q.number } { Path( q.scan ).name }" + ( f" ({ q.looked_like })" if q.looked_like else "" )
                            for q in waiting )
        return f"{ lead }Scala is waiting on { len( waiting )}: { listed }."


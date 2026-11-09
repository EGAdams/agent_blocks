"""QuestionRetry: ask Haiku again about pages it never actually saw.

A Haiku call that fails (a timeout, the SDK's 1 MiB stream cap before
2026-11-02) is still an answer to ScalaDesk -- UNSURE, so the scan goes to the
inbox, a question goes on the to-do list, and the ledger settles it as INBOX.
Nothing ever looks at it again, and the question quotes the error to EG.

This walks the open questions whose reason says the model was never reached
(`NOT_ASKED`), reads each page again, and classifies it the same way a fresh
scan is classified:

- sure, allowed, and not finance  -> filed, to-do checked off, no lesson
  (a lesson is what EG taught, and EG taught nothing here);
- anything else                   -> the question keeps waiting for EG, but
  says what the page now looks like instead of quoting the old error;
- the model still can't be asked  -> left exactly as it was.

One line per question, for Toyota or the operator to read.
"""
from __future__ import annotations

from pathlib import Path

from .desk import ScalaDesk
from .interfaces import IFilingCabinet
from .models import FINANCE, UNSURE, Classification
from .questions import Question, QuestionDesk


class QuestionRetry:
    def __init__( self, desk: ScalaDesk, questions: QuestionDesk, cabinet: IFilingCabinet ) -> None:
        self._desk = desk
        self._questions = questions
        self._cabinet = cabinet

    def retry_all( self ) -> list[ str ]:
        waiting = self._questions.unclassified()
        if not waiting:
            return [ "Scala has no questions waiting on a failed Haiku call." ]
        return [ self.retry( question ) for question in waiting ]

    def retry( self, question: Question ) -> str:
        image = self._questions.image_of( question )
        if image is None:
            return f"#{ question.number }: the scan is gone ({ Path( question.scan ).name }); left as it was."
        verdict = self._desk.classify( self._desk.read_text( image ), image )
        if not verdict.answered:
            return f"#{ question.number }: Haiku still could not be asked ({ verdict.reason[ :120 ]}); left as it was."
        if self._files( verdict ):
            return self._questions.file_as_classified( question, verdict )
        revised = self._questions.revise( question, self._looked_like( verdict ))
        return f"#{ question.number } still needs EG: { revised.looked_like }"

    def _files( self, verdict: Classification ) -> bool:
        return ( verdict.folder not in ( FINANCE, UNSURE, "" ) and self._desk.is_sure( verdict )
                 and self._cabinet.is_allowed( verdict.folder ))

    @staticmethod
    def _looked_like( verdict: Classification ) -> str:
        reason = verdict.reason.rstrip( "." )[ :160 ]
        if verdict.folder == FINANCE:
            return "; ".join( p for p in ( reason, "looks financial -- Mazda's, or EG decides" ) if p )
        guess = f"best guess { verdict.folder } ({ verdict.confidence:.2f})" if verdict.folder not in ( UNSURE, "" ) else ""
        return "; ".join( p for p in ( reason, guess ) if p ) or "Haiku couldn't tell"

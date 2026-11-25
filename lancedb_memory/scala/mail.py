"""Scala's questions by email: the stamp, the ports, the asker decorator, the book.

When Scala opens a question she still puts it on the company to-do list, and
`EmailingAsker` (a Decorator over any IAskPerson) also mails it to EG through
the team post office (Yolanda operates the mailbox). The subject carries the
postage stamp `[Scala #N]`, which is how a reply finds its way back: the
mail check (`adws/adw_interface_scala_mail_replies.py`) asks Yolanda for
replies bearing a stamp and posts each one to Scala through the post office.

Everything mailed and every reply handled is one line in `~/scala/mail.jsonl`
(0600), so a reply is handed to Scala once, never twice.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .ledger import default_home
from .questions import IAskPerson, _append, _lines

DEFAULT_TO = "eg@americansjewelry.com"

_STAMP = re.compile( r"\[\s*Scala\s*#\s*(\d+)\s*\]", re.I )


def recipient() -> str:
    return os.environ.get( "SCALA_QUESTION_EMAIL", DEFAULT_TO ).strip()


def stamp( number: int ) -> str:
    return f"[Scala #{ number }]"


def read_stamp( subject: str ) -> int | None:
    found = _STAMP.search( subject or "" )
    return int( found.group( 1 )) if found else None


# -- what crosses the mailbox ------------------------------------------------

class MailedReply( BaseModel ):
    """One reply EG sent to a stamped question, as Yolanda read it."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    message_id: str
    thread_id:  str
    number:     int
    text:       str                         # only what EG wrote, quoted mail removed


class MailEntry( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    kind:       Literal[ "sent", "handled" ]
    number:     int
    at:         datetime
    to:         str = ""
    task_id:    str = ""                    # the post-office task that carried it
    message_id: str = ""                    # handled: the reply's Gmail id
    line:       str = ""                    # handled: what Scala said back


class IQuestionMailer( Protocol ):
    def send_question( self, number: int, text: str ) -> str:
        """Post the question for mailing; return the post-office task id."""
        ...


class IReplyInbox( Protocol ):
    def replies( self, numbers: list[ int ], since: datetime ) -> list[ MailedReply ]:
        """Replies EG sent to the stamped questions `numbers`, newest last."""
        ...

    def confirm( self, reply: MailedReply, line: str ) -> str:
        """Answer EG in the reply's thread with Scala's line; return the task id."""
        ...


class IScalaPost( Protocol ):
    def deliver( self, reply: MailedReply, sentence: str ) -> tuple[ bool, str ]:
        """Hand EG's answer to Scala through the post office; (ok, Scala's line)."""
        ...


# -- the book ----------------------------------------------------------------

class JsonlMailBook:
    def __init__( self, home: Path | None = None ) -> None:
        self._home = home or default_home()
        self._path = self._home / "mail.jsonl"

    def add( self, entry: MailEntry ) -> None:
        _append( self._home, self._path, entry.model_dump_json())

    def entries( self ) -> list[ MailEntry ]:
        found: list[ MailEntry ] = []
        for line in _lines( self._path ):
            try:
                found.append( MailEntry.model_validate_json( line ))
            except ValueError:
                continue
        return found


class InMemoryMailBook:
    def __init__( self ) -> None:
        self.items: list[ MailEntry ] = []

    def add( self, entry: MailEntry ) -> None:
        self.items.append( entry )

    def entries( self ) -> list[ MailEntry ]:
        return list( self.items )


# -- asking ------------------------------------------------------------------

class EmailingAsker:
    """IAskPerson: ask the inner asker (it numbers the question), then mail it.

    A mail that cannot be posted never loses the question: it is already on
    the to-do list, and the failure is one line on stderr."""

    def __init__( self, inner: IAskPerson, mailer: IQuestionMailer, book: JsonlMailBook | InMemoryMailBook,
                  to: str | None = None ) -> None:
        self._inner = inner
        self._mailer = mailer
        self._book = book
        self._to = to or recipient()

    def ask( self, text: str ) -> int:
        number = self._inner.ask( text )
        try:
            task_id = self._mailer.send_question( number, text )
        except Exception as exc:  # noqa: BLE001 -- the to-do item already holds the question
            print( f"scala: could not mail question #{ number }: { exc }", file=sys.stderr )
            return number
        self._book.add( MailEntry( kind="sent", number=number, at=datetime.now().astimezone(),
                                   to=self._to, task_id=task_id ))
        return number

    def close( self, number: int ) -> None:
        self._inner.close( number )

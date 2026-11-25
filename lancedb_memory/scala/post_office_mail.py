"""The post-office side of Scala's email: Yolanda mails, Yolanda reads, Scala answers.

`PostOfficeMail` is the one adapter that knows the team post office
(`agent_comms`). It satisfies all three ports in `scala.mail`:

* IQuestionMailer -- scala -> yolanda, `message`, not waited on (a scan run
  never blocks on Gmail);
* IReplyInbox     -- scala -> yolanda, `ask`, waited on: a read-only search
  for stamped replies, answered as `REPLY|...` lines; and the confirmation
  sent back in the same thread;
* IScalaPost      -- yolanda -> scala, `answer`: Scala's own card runs
  `adw_interface_scala_answer.py`, which files the scan and says one line.

agent_comms is imported on first use: building a scan run must not drag it in.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .mail import MailedReply, read_stamp, recipient, stamp

_TELL_TOYOTA = re.compile( r"\s*Tell Toyota:.*$", re.S )
_REPLY_LINE = re.compile( r"^\s*REPLY\|([^|]+)\|([^|]+)\|([^|]+)\|(.*)$" )
_QUOTED = re.compile( r"^\s*(>|On .+ wrote:\s*$|-{2,}\s*Original Message)", re.I )


def _default_ops() -> dict[ str, Any ]:
    from agent_comms.factory import build_post_office
    from agent_comms.operations.catalog import operations
    return operations( build_post_office())


def new_text( text: str ) -> str:
    """What EG wrote, without the quoted mail under it."""
    kept: list[ str ] = []
    for line in text.replace( "\\n", "\n" ).splitlines():
        if _QUOTED.match( line ):
            break
        kept.append( line )
    return " ".join( " ".join( kept ).split())


def parse_replies( answer: str, numbers: list[ int ]) -> list[ MailedReply ]:
    """Yolanda's `REPLY|id|thread|subject|text` lines; anything else is ignored."""
    found: list[ MailedReply ] = []
    for line in answer.splitlines():
        match = _REPLY_LINE.match( line )
        if not match:
            continue
        number = read_stamp( match.group( 3 ))
        text = new_text( match.group( 4 ))
        if number is None or number not in numbers or not text:
            continue
        found.append( MailedReply( message_id=match.group( 1 ).strip(), thread_id=match.group( 2 ).strip(),
                                   number=number, text=text[ :1500 ]))
    return found


class PostOfficeMail:
    def __init__( self, folders: Callable[ [], tuple[ str, ... ]] = lambda: (),
                  ops_factory: Callable[ [], dict[ str, Any ]] = _default_ops,
                  to: str | None = None, wait_seconds: float = 240.0 ) -> None:
        self._folders = folders
        self._ops_factory = ops_factory
        self._ops: dict[ str, Any ] | None = None
        self._to = to or recipient()
        self._wait = wait_seconds

    # -- IQuestionMailer ----------------------------------------------------

    def send_question( self, number: int, text: str ) -> str:
        question = _TELL_TOYOTA.sub( "", text ).strip()
        kinds = ", ".join( f for f in self._folders() if "/" in f ) or "bills_511/<kind>, documents_511/<kind>"
        body = ( f"Scala's question #{ number }.\n\n{ question }\n\n"
                 f"Reply to this email with the folder it goes in, for example: bills_511/gas. "
                 f"A new folder is fine too, one level under bills_511/ or documents_511/.\n"
                 f"Folders Scala has now: { kinds }." )
        outcome = self._run( sender="scala", recipient="yolanda", task_type="message", wait_seconds=0,
                             text=( f"Send this email now (this is the instruction to send it; do not ask). "
                                    f"To: { self._to }. Subject: { stamp( number ) } Scala needs a folder for a scan. "
                                    f"Body, exactly:\n{ body }" ))
        return str( outcome.data.get( "task_id", "" ))

    # -- IReplyInbox ---------------------------------------------------------

    def replies( self, numbers: list[ int ], since: datetime ) -> list[ MailedReply ]:
        if not numbers:
            return []
        after = since.strftime( "%Y/%m/%d" )
        stamps = " OR ".join( f'"Scala #{ n }"' for n in numbers )
        outcome = self._run( sender="scala", recipient="yolanda", task_type="ask", wait_seconds=self._wait,
                             text=( f"Read-only Gmail search; send nothing, draft nothing, change nothing. "
                                    f"Search: from:{ self._to } after:{ after } subject:({ stamps }). "
                                    f"For every message found, write exactly one line: "
                                    f"REPLY|<message id>|<thread id>|<subject>|<only the new text the sender "
                                    f"wrote, quoted earlier mail removed, on one line>. "
                                    f"If none are found write exactly: NO_REPLIES" ))
        if not outcome.ok or outcome.data.get( "pending" ):
            raise RuntimeError( f"Yolanda could not search the mail: { outcome.line }" )
        return parse_replies( outcome.line, numbers )

    def confirm( self, reply: MailedReply, line: str ) -> str:
        outcome = self._run( sender="scala", recipient="yolanda", task_type="message", wait_seconds=0,
                             text=( f"Reply now (this is the instruction to send it; do not ask) to Gmail message "
                                    f"{ reply.message_id } in thread { reply.thread_id }, to { self._to }, keeping "
                                    f"the subject { stamp( reply.number ) }. Body, exactly:\n{ line }" ))
        return str( outcome.data.get( "task_id", "" ))

    # -- IScalaPost ----------------------------------------------------------

    def deliver( self, reply: MailedReply, sentence: str ) -> tuple[ bool, str ]:
        outcome = self._run( sender="yolanda", recipient="scala", task_type="answer", wait_seconds=120,
                             text=sentence, context={ "gmail_message_id": reply.message_id })
        if outcome.data.get( "pending" ):
            return False, f"Scala has not answered yet on question #{ reply.number }."
        return bool( outcome.ok ), _verdictless( outcome.line )

    # -- the post office -----------------------------------------------------

    def _run( self, **request: Any ):
        if self._ops is None:
            self._ops = self._ops_factory()
        return self._ops[ "send_and_capture" ].run( request )


def _verdictless( line: str ) -> str:
    return re.sub( r"^(DONE|NO_CHANGE|REFUSED):\s*", "", line.strip())


def emailing_asker( inner, cabinet ):
    """The asker a scan run uses: the to-do list, and an email to EG on top.

    `SCALA_EMAIL_QUESTIONS=0` turns the email off (the to-do item still goes up)."""
    import os
    from .mail import EmailingAsker, JsonlMailBook
    if os.environ.get( "SCALA_EMAIL_QUESTIONS", "1" ) == "0":
        return inner
    return EmailingAsker( inner, PostOfficeMail( folders=cabinet.folders ), JsonlMailBook())

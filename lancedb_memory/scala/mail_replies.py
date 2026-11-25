"""ReplyCourier: carry EG's emailed answers to Scala, once each, and tell him what she did.

    open mailed questions -> Yolanda finds stamped replies -> each new one goes
    to Scala through the post office -> Scala's line goes back in the thread

With no mailed question still open it asks nobody anything (no model call),
so a frequent timer costs nothing while Scala is waiting on nobody.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .mail import IReplyInbox, IScalaPost, JsonlMailBook, InMemoryMailBook, MailEntry, MailedReply
from .questions import Question, QuestionState, read_answer


def sentence_for( reply: MailedReply, folders: tuple[ str, ... ]) -> str:
    """The sentence Scala reads: the stamp's number first, then EG's words.

    "gas" alone names the folder when exactly one folder Scala has ends in
    that word, so a one-word email reply works the way it reads."""
    sentence = f"Scala, question #{ reply.number }: { reply.text }"
    if read_answer( reply.text ).folder:
        return sentence
    words = set( re.findall( r"[a-z0-9_]+", reply.text.lower()))
    leaves = [ f for f in folders if "/" in f and f.split( "/", 1 )[ 1 ] in words ]
    return f"{ sentence } (under { leaves[ 0 ]})" if len( leaves ) == 1 else sentence


@dataclass
class CourierReport:
    looked_for: list[ int ] = field( default_factory=list )
    handled:    list[ str ] = field( default_factory=list )

    def line( self ) -> str:
        if not self.looked_for:
            return "No emailed Scala question is open, so no mail was checked."
        asked = ", ".join( f"#{ n }" for n in self.looked_for )
        if not self.handled:
            return f"Checked the mail for replies to { asked }: none yet."
        return " ".join( self.handled )


class ReplyCourier:
    def __init__( self, questions: list[ Question ], book: JsonlMailBook | InMemoryMailBook,
                  inbox: IReplyInbox, post: IScalaPost, folders: tuple[ str, ... ] = ()) -> None:
        self._questions = questions
        self._book = book
        self._inbox = inbox
        self._post = post
        self._folders = folders

    def waiting( self ) -> tuple[ list[ int ], datetime | None ]:
        """Open questions that were mailed, and when the oldest went out."""
        entries = self._book.entries()
        sent = { e.number: e.at for e in entries if e.kind == "sent" }
        open_numbers = sorted( q.number for q in self._questions
                               if q.state is QuestionState.OPEN and q.number in sent )
        since = min(( sent[ n ] for n in open_numbers ), default=None )
        return open_numbers, since

    def run( self ) -> CourierReport:
        numbers, since = self.waiting()
        report = CourierReport( looked_for=numbers )
        if not numbers or since is None:
            return report
        done = { e.message_id for e in self._book.entries() if e.kind == "handled" }
        for reply in self._inbox.replies( numbers, since ):
            if reply.message_id in done:
                continue
            ok, line = self._post.deliver( reply, sentence_for( reply, self._folders ))
            if not ok and line.startswith( "Scala has not answered yet" ):
                report.handled.append( line )           # not marked: the next check tries again
                continue
            self._inbox.confirm( reply, line )
            self._book.add( MailEntry( kind="handled", number=reply.number, at=datetime.now().astimezone(),
                                       message_id=reply.message_id, line=line[ :500 ]))
            done.add( reply.message_id )
            report.handled.append( line )
        return report

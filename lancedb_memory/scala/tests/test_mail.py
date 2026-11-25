"""Offline tests for Scala's email questions. Run from lancedb_memory/:

    .venv/bin/python -m scala.tests.test_mail

No mail, no post office: Yolanda and Scala's card are fakes behind the ports,
and the post office's operations are a recording stub.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from scala.mail import EmailingAsker, InMemoryMailBook, MailedReply, MailEntry, read_stamp, stamp
from scala.mail_replies import ReplyCourier, sentence_for
from scala.post_office_mail import PostOfficeMail, new_text, parse_replies
from scala.questions import Question, QuestionState

NOW = datetime( 2026, 11, 25, 9, 0 ).astimezone()
FOLDERS = ( "bills_511", "bills_511/electric", "bills_511/gas", "documents_511", "documents_511/insurance" )


class ListAsker:
    def __init__( self ) -> None:
        self.asked: list[ str ] = []
        self.closed: list[ int ] = []

    def ask( self, text: str ) -> int:
        self.asked.append( text )
        return 10 + len( self.asked ) - 1

    def close( self, number: int ) -> None:
        self.closed.append( number )


class FakeMailer:
    def __init__( self, fail: bool = False ) -> None:
        self.sent: list[ tuple[ int, str ]] = []
        self.fail = fail

    def send_question( self, number: int, text: str ) -> str:
        if self.fail:
            raise RuntimeError( "post office down" )
        self.sent.append(( number, text ))
        return f"task-{ number }"


class FakeInbox:
    def __init__( self, replies: list[ MailedReply ]) -> None:
        self._replies = replies
        self.searched: list[ list[ int ]] = []
        self.confirmed: list[ tuple[ str, str ]] = []

    def replies( self, numbers, since ):
        self.searched.append( numbers )
        return [ r for r in self._replies if r.number in numbers ]

    def confirm( self, reply, line ):
        self.confirmed.append(( reply.message_id, line ))
        return "task-confirm"


class FakeScala:
    def __init__( self, line: str = "Scala filed question #10 under bills_511/gas." ) -> None:
        self.sentences: list[ str ] = []
        self.line = line

    def deliver( self, reply, sentence ):
        self.sentences.append( sentence )
        return True, self.line


def question( number: int, state: QuestionState = QuestionState.OPEN ) -> Question:
    return Question( number=number, asked_at=NOW, scan=f"/inbox/{ number }.jpg", source=f"/in/{ number }.jpg",
                     sha256="0" * 64, looked_like="a Consumers Energy notice", hints=(), state=state )


def reply( number: int = 10, text: str = "bills_511/gas", message_id: str = "m1" ) -> MailedReply:
    return MailedReply( message_id=message_id, thread_id="t1", number=number, text=text )


def sent_book( *numbers: int ) -> InMemoryMailBook:
    book = InMemoryMailBook()
    for n in numbers:
        book.add( MailEntry( kind="sent", number=n, at=NOW, to="eg@example.com", task_id=f"task-{ n }" ))
    return book


# -- the stamp and the asker -------------------------------------------------

async def test_stamp_round_trips_through_a_reply_subject():
    assert stamp( 10 ) == "[Scala #10]"
    assert read_stamp( "Re: [Scala #10] Scala needs a folder" ) == 10
    assert read_stamp( "RE: FW: [ scala # 7 ]" ) == 7
    assert read_stamp( "Linux file bak" ) is None


async def test_asking_puts_it_on_the_list_then_mails_it_with_the_same_number():
    inner, mailer, book = ListAsker(), FakeMailer(), InMemoryMailBook()
    number = EmailingAsker( inner, mailer, book, to="eg@example.com" ).ask( "Scala needs a folder" )
    assert number == 10 and inner.asked == [ "Scala needs a folder" ]
    assert mailer.sent == [( 10, "Scala needs a folder" )]
    assert [( e.kind, e.number, e.task_id ) for e in book.entries()] == [( "sent", 10, "task-10" )]


async def test_a_mail_that_cannot_be_posted_still_leaves_the_question_asked():
    inner, book = ListAsker(), InMemoryMailBook()
    assert EmailingAsker( inner, FakeMailer( fail=True ), book, to="x@example.com" ).ask( "q" ) == 10
    assert inner.asked == [ "q" ] and book.entries() == []


async def test_closing_passes_through_to_the_list():
    inner = ListAsker()
    EmailingAsker( inner, FakeMailer(), InMemoryMailBook(), to="x@example.com" ).close( 10 )
    assert inner.closed == [ 10 ]


# -- reading Yolanda's answer ------------------------------------------------

async def test_replies_are_read_from_yolanda_lines_only_for_asked_numbers():
    answer = ( "Here is what I found:\n"
               "REPLY|m1|t1|Re: [Scala #10] Scala needs a folder|gas please\n"
               "REPLY|m2|t2|Re: [Scala #4] old|bills_511/water\n"
               "REPLY|m3|t3|Linux file bak|nothing\n" )
    found = parse_replies( answer, [ 10 ])
    assert found == [ MailedReply( message_id="m1", thread_id="t1", number=10, text="gas please" )]
    assert parse_replies( "NO_REPLIES", [ 10 ]) == []


async def test_quoted_mail_is_cut_so_scalas_own_words_are_not_read_as_the_answer():
    text = "electric\\nOn Tue, Nov 25, 2026 at 7:23 AM <eg1972@gmail.com> wrote:\\n> bills_511/gas and bills_511/electric"
    assert new_text( text ) == "electric"
    assert new_text( "bills_511/gas\n> quoted bills_511/electric" ) == "bills_511/gas"


async def test_a_one_word_reply_names_the_one_folder_ending_in_it():
    assert sentence_for( reply( text = "gas" ), FOLDERS ).endswith( "(under bills_511/gas)" )
    assert sentence_for( reply( text = "it is bills_511/electric" ), FOLDERS ) == \
        "Scala, question #10: it is bills_511/electric"
    assert "(under" not in sentence_for( reply( text = "not sure" ), FOLDERS )


# -- the courier -------------------------------------------------------------

async def test_nothing_is_searched_while_no_mailed_question_is_open():
    inbox = FakeInbox([ reply()])
    report = ReplyCourier([ question( 9 ), question( 10, QuestionState.ANSWERED )], sent_book( 10 ),
                          inbox, FakeScala(), FOLDERS ).run()
    assert inbox.searched == [] and "no mail was checked" in report.line()


async def test_a_reply_goes_to_scala_once_and_her_line_goes_back_in_the_thread():
    book, inbox, scala = sent_book( 10 ), FakeInbox([ reply()]), FakeScala()
    courier = ReplyCourier([ question( 10 )], book, inbox, scala, FOLDERS )
    assert courier.run().line() == "Scala filed question #10 under bills_511/gas."
    assert scala.sentences == [ "Scala, question #10: bills_511/gas" ]
    assert inbox.confirmed == [( "m1", "Scala filed question #10 under bills_511/gas." )]
    courier.run()                                     # the same reply, seen again
    assert len( scala.sentences ) == 1 and len( inbox.confirmed ) == 1


async def test_an_unanswered_hand_off_is_tried_again_next_time():
    class Slow( FakeScala ):
        def deliver( self, reply, sentence ):
            self.sentences.append( sentence )
            return False, "Scala has not answered yet on question #10."
    book, inbox, scala = sent_book( 10 ), FakeInbox([ reply()]), Slow()
    ReplyCourier([ question( 10 )], book, inbox, scala ).run()
    ReplyCourier([ question( 10 )], book, inbox, scala ).run()
    assert len( scala.sentences ) == 2 and inbox.confirmed == []


async def test_search_starts_at_the_oldest_open_mailed_question():
    book = sent_book( 10 )
    book.add( MailEntry( kind="sent", number=11, at=NOW - timedelta( days=2 )))
    numbers, since = ReplyCourier([ question( 10 ), question( 11 )], book, FakeInbox([]), FakeScala()).waiting()
    assert numbers == [ 10, 11 ] and since == NOW - timedelta( days=2 )


# -- the post-office adapter -------------------------------------------------

class RecordingOps:
    def __init__( self, line: str = "", ok: bool = True, data: dict | None = None ) -> None:
        self.requests: list[ dict ] = []
        self._outcome = SimpleNamespace( ok=ok, line=line, data=data or { "task_id": "T1" })

    def __getitem__( self, name ):
        assert name == "send_and_capture"
        return self

    def run( self, request ):
        self.requests.append( request )
        return self._outcome


async def test_the_question_goes_to_yolanda_as_a_send_instruction_with_the_stamp():
    ops = RecordingOps()
    mail = PostOfficeMail( folders=lambda: FOLDERS, ops_factory=lambda: ops, to="eg@example.com" )
    assert mail.send_question( 10, "Scala needs a folder for x.jpg. Tell Toyota: \"Scala, file #<n>\"." ) == "T1"
    request = ops.requests[ 0 ]
    assert ( request[ "sender" ], request[ "recipient" ], request[ "task_type" ]) == ( "scala", "yolanda", "message" )
    assert request[ "wait_seconds" ] == 0 and "[Scala #10]" in request[ "text" ]
    assert "To: eg@example.com" in request[ "text" ] and "Tell Toyota" not in request[ "text" ]
    assert "bills_511/gas" in request[ "text" ]


async def test_an_answer_reaches_scala_on_her_card_from_yolanda():
    ops = RecordingOps( line="DONE: Scala filed question #10 under bills_511/gas." )
    ok, line = PostOfficeMail( ops_factory=lambda: ops, to="eg@example.com" ).deliver( reply(), "Scala, #10 gas" )
    request = ops.requests[ 0 ]
    assert ( request[ "sender" ], request[ "recipient" ], request[ "task_type" ]) == ( "yolanda", "scala", "answer" )
    assert ok and line == "Scala filed question #10 under bills_511/gas."


async def test_a_failed_search_is_an_error_not_no_replies():
    ops = RecordingOps( line="Gmail is not connected", ok=False )
    mail = PostOfficeMail( ops_factory=lambda: ops, to="eg@example.com" )
    try:
        mail.replies([ 10 ], NOW )
    except RuntimeError as exc:
        assert "Gmail is not connected" in str( exc )
    else:
        raise AssertionError( "a failed search must not read as 'no replies'" )


if __name__ == "__main__":
    from tests.runner import main
    main( globals())

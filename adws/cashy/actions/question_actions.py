"""Answers to the question Cashy asked: a month, a bank, an account, a
choice, or yes / no to pressing something consequential."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Intent
from .common import back_to_rest, pick, same_option
from .page_actions import press_and_tell

if TYPE_CHECKING:
    from ..turn import Turn


class AnswerAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s, q = turn.session, turn.session.question
        if q is None:
            if intent.account and intent.bank:
                turn.services.accounts.remember( intent.account, intent.bank )
                turn.say( f"Noted: the account ending { intent.account } is at { turn.services.banks.by_key( intent.bank ).title }." )
            else:
                turn.say( "I wasn't waiting for an answer. Say 'help' to see what I can do." )
            return
        resumed = self._resumed( intent, turn )
        if resumed is None:
            turn.say( q.prompt )
            turn.halted = True
            return
        back_to_rest( turn )
        turn.handle( resumed )

    def _resumed( self, intent: Intent, turn: "Turn" ) -> Intent | None:
        q, services = turn.session.question, turn.services
        if q.about == "period":
            return q.resume.model_copy( update={ "period": intent.period }) if intent.period else None
        if q.about == "bank":
            bank = intent.bank or ( services.banks.find( intent.text ).key if services.banks.find( intent.text ) else "" )
            return q.resume.model_copy( update={ "bank": bank }) if bank else None
        if q.about == "account":
            bank = intent.bank or ( services.banks.find( intent.text ).key if services.banks.find( intent.text ) else "" )
            if not bank:
                return None
            services.accounts.remember( q.resume.account, bank )
            turn.say( f"Noted: the account ending { q.resume.account } is at { services.banks.by_key( bank ).title }. "
                      "I'll remember that." )
            return q.resume.model_copy( update={ "bank": bank })
        if q.about == "transaction":
            hint = pick( q.hints, intent.number or 1 )
            if hint is None:
                return None
            return Intent( kind="find_transaction", target=hint.payee, account=hint.accounts[ 0 ] if hint.accounts else "",
                           text="resume" )
        return None


class ConfirmAction:
    """Only the person at the keyboard can say yes to a consequential press."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        q = turn.session.question
        if q is None:
            turn.say( "There's nothing waiting for a yes." )
            return
        if q.about == "approval":
            if turn.actor != "user":
                turn.fail( "Only the person at the keyboard can approve that." )
                return
            page  = turn.read_page()
            fresh = same_option( page, q.choices[ 0 ]) if q.choices else None
            back_to_rest( turn )
            if fresh is None:
                turn.say( "It isn't on the page any more, so I've pressed nothing." )
                return
            turn.services.clicker.press( turn, fresh, approved=True )
            turn.say( f"Pressed '{ fresh.text }' as you approved. Now on '{ turn.browser().title }'." )
            return
        if q.about in ( "transaction", "choice" ):
            turn.services.actions.action( "click_number" ).run( intent.model_copy( update={ "number": 1 }), turn )
            return
        turn.say( q.prompt )
        turn.halted = True


class DenyAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        q = turn.session.question
        if q is None:
            turn.say( "OK." )
            return
        back_to_rest( turn )
        if turn.session.task.status == "waiting":
            turn.session.task.status, turn.session.task.detail = "completed", "you said no"
        turn.say( "OK, I've left it unpressed." if q.about == "approval" else "OK. Tell me what you'd like instead." )
        turn.halted = True

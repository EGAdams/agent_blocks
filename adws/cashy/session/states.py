"""The GoF State objects: what a sentence means while a session rests here.

A state decides *whether* a request makes sense now and hands it to its
command; the commands decide *how*. Each state lists what it accepts, so
"click Statements" before anyone has logged in gets an explanation, not a
click.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Intent, IntentKind, SessionStateName

if TYPE_CHECKING:
    from ..turn import Turn

#: Understood in every state.
ALWAYS: frozenset[ IntentKind ] = frozenset({ "status", "help", "stop", "pause", "unknown" })
#: Asking for a bank page: needs a signed-in session.
SIGNED_IN: frozenset[ IntentKind ] = frozenset({ "go_to", "click", "click_number", "list_options", "go_back",
                                                 "try_another", "get_statement", "find_transaction",
                                                 "remember", "save_adw" })
#: Starting something new.
STARTS: frozenset[ IntentKind ] = frozenset({ "open_bank", "get_statement", "find_transaction" })


class BaseState:
    name: SessionStateName = "idle"
    accepts: frozenset[ IntentKind ] = ALWAYS

    def handle( self, intent: Intent, turn: "Turn" ) -> None:
        if intent.kind in self.accepts:
            turn.run( intent )
        else:
            turn.say( self.refusal( intent, turn ))
            turn.halted = True

    def refusal( self, intent: Intent, turn: "Turn" ) -> str:
        return "I can't do that right now. Say 'status' to see where we are."


class IdleState( BaseState ):
    """No bank open. Reading or clicking a page Cashy has not been asked to
    open is allowed (a person may have opened it by hand); a request that
    needs a bank names which one."""
    name    = "idle"
    accepts = ALWAYS | STARTS | { "logged_in", "list_options", "click", "click_number", "go_back", "go_to",
                                  "resume", "remember", "save_adw", "answer" }


class AwaitingLoginState( BaseState ):
    """The login page is open and the person is signing in. Cashy waits; the
    only page request it takes is 'I'm logged in', which it checks."""
    name    = "awaiting_user_login"
    accepts = ALWAYS | { "logged_in", "open_bank", "list_options", "resume", "get_statement", "find_transaction",
                         "go_to", "answer" }

    def refusal( self, intent: Intent, turn: "Turn" ) -> str:
        return "I'm waiting for you to sign in. Tell me when you're logged in."


class ReadyState( BaseState ):
    name    = "ready"
    accepts = ALWAYS | STARTS | SIGNED_IN | { "logged_in", "resume", "answer" }


class AwaitingInputState( BaseState ):
    """A question is open. An answer goes to it; anything else is a new
    request and the question is dropped -- the person changed their mind."""
    name = "awaiting_user_input"

    def handle( self, intent: Intent, turn: "Turn" ) -> None:
        if intent.kind in ( "answer", "confirm", "deny" ) or ( intent.kind == "click_number" and turn.session.question ):
            turn.run( intent )
            return
        if intent.kind not in ALWAYS | { "resume" }:
            turn.session.question = None
            turn.move( turn.session.asked_from or _resting( turn ))
            turn.handle( intent )
            return
        turn.run( intent )


class PausedState( BaseState ):
    name    = "paused"
    accepts = ALWAYS | { "resume" }

    def refusal( self, intent: Intent, turn: "Turn" ) -> str:
        return "I'm paused. Say 'resume' to carry on, or 'stop' to end the task."


def _resting( turn: "Turn" ) -> SessionStateName:
    return "ready" if turn.session.bank and turn.session.task.goal != "login" else "idle"


ALL_STATES = ( IdleState(), AwaitingLoginState(), ReadyState(), AwaitingInputState(), PausedState())

"""Commands on the page in front of the person: read it, click, go back,
go somewhere by name, try another way."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import CashyTask, Intent, Question
from ..page.ranker import ACTIONABLE
from .common import back_to_rest, describe, ensure_signed_in, pick, same_option

if TYPE_CHECKING:
    from ..turn import Turn

CANNOT_READ = ( "I can't find anything to click in this page's HTML. It may be drawn on a canvas or inside "
                "a frame I can't read. I won't guess from a picture -- could you click through this part "
                "yourself and tell me when you're past it?" )


class ListOptionsAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        page = turn.read_page()
        if not any( o.kind in ACTIONABLE for o in page.options ):
            turn.say( CANNOT_READ )
            turn.halted = True
            return
        turn.say( *describe( page ))
        if page.signed_in is False:
            turn.say( "This page has a sign-in form; sign in yourself if you want to go further." )


class ClickAction:
    """'Click Statements': the option whose words match best -- asked about
    when two are close, never guessed."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        page   = turn.read_page()
        ranked = turn.services.ranker.rank( page.options, [ intent.target ])
        if not ranked:
            turn.say( f"I don't see '{ intent.target }' on this page.", *describe( page, 10 ))
            turn.halted = True
            return
        if len( ranked ) > 1 and ranked[ 0 ].score - ranked[ 1 ].score < 0.5:
            choices = [ r.option for r in ranked[ :4 ]]
            turn.ask( Question( about="choice", choices=choices, prompt=f"More than one thing matches '{ intent.target }':\n"
                                + "\n".join( f"  { i }. { o.text }" for i, o in enumerate( choices, 1 ))
                                + "\nWhich one? (say the number)" ))
            return
        press_and_tell( turn, ranked[ 0 ].option )


class ClickNumberAction:
    """'Click the second one': from the open question's choices if there is
    one, else from the page as Cashy last described it."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        question = s.question
        if question and question.about == "transaction":
            turn.services.actions.action( "answer" ).run( intent, turn )
            return
        if question and question.choices:
            old = pick( question.choices, intent.number )
        elif intent.number == -1:
            old = pick([ o for o in s.options if o.kind in ACTIONABLE ], -1 )
        else:
            old = next(( o for o in s.options if o.number == intent.number ), None )
        if old is None:
            turn.say( f"There's no number { intent.number } in what I listed. Say 'what options do you see?' to see them again." )
            turn.halted = True
            return
        page  = turn.read_page()
        fresh = same_option( page, old )
        if fresh is None:
            turn.say( f"'{ old.text }' isn't on the page any more.", *describe( page, 10 ))
            turn.halted = True
            return
        resume = question.resume if question else None
        back_to_rest( turn )
        if press_and_tell( turn, fresh ) and resume is not None:
            turn.handle( resume )


class GoBackAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        browser = turn.browser()
        turn.event( "navigating", "back" )
        browser.go_back()
        for step in reversed( turn.session.trail ):
            if not step.undone:
                step.undone = True
                break
        turn.say( f"Back on '{ browser.title or browser.current_url }'." )


class GoToAction:
    """'Open my statements': a learned route if there is one (Mode B), else
    a guided exploration (Mode A)."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        goal = intent.goal
        s    = turn.session
        same = s.task.goal == goal and s.task.status in ( "running", "waiting" )
        task = s.task.model_copy( update={ "status": "running" }) if same else CashyTask( goal=goal, status="running" )
        if not ensure_signed_in( turn, intent, task ):
            return
        s.task = task
        arrive( turn, goal )


class TryAnotherAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        goal = "statements" if s.task.goal == "statement_download" else s.task.goal
        if not goal or goal in ( "login", "transaction" ):
            turn.say( "There's no route I'm trying right now. Tell me where you'd like to go." )
            return
        if any( not t.undone for t in s.trail ):
            turn.services.actions.action( "go_back" ).run( intent, turn )
        s.task.status = "running"
        s.task.mode   = "discovery"
        if turn.services.explorer.explore( turn, goal ) == "lost":
            turn.say( "I've run out of routes I'm confident about. Say 'what options do you see?' and pick one." )
            s.task.status = "waiting"


def press_and_tell( turn: "Turn", option ) -> bool:
    if not turn.services.clicker.press( turn, option ):
        return False
    browser = turn.browser()
    turn.say( f"Clicked '{ option.text }'. Now on '{ browser.title or browser.current_url }'." )
    return True


def arrive( turn: "Turn", goal: str ) -> bool:
    """Get to `goal`'s page by the best known way. True once there."""
    s = turn.session
    workflow = turn.services.workflows.resolve( s.bank, goal )
    if workflow:
        s.task.mode = "playback"
        result = turn.services.playback.run( turn, workflow, None )
        if result.ok:
            turn.say( f"Used the saved route '{ workflow.playback }'." )
            page = turn.read_page()
            turn.say( f"Here's '{ page.title }'." )
            s.task.status = "completed"
            return True
        case = turn.services.reporter.report( turn, f"'{ workflow.playback }' reaches { goal }", result.message )
        turn.say( f"The saved route didn't work ({ result.message }). I've written it up for the Mechanic "
                  f"({ case }) and I'll find the way step by step." )
        s.task.mode = "discovery"
    outcome = turn.services.explorer.explore( turn, goal )
    if outcome == "arrived":
        page = turn.read_page()
        s.task.status = "completed" if s.task.goal == goal else s.task.status
        turn.say( f"Here's '{ page.title }'." )
        return True
    if outcome == "lost":
        s.task.status = "failed"
        s.task.detail = f"no route to { goal.replace( '_', ' ' ) } found"
        turn.say( f"I couldn't find the way to { goal.replace( '_', ' ' ) } from here. "
                  "Say 'what options do you see?' and tell me which one to click." )
        turn.halted = True
    else:
        s.task.status = "waiting"
    return False

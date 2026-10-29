"""Commands about the conversation itself: status, help, stop, pause, resume."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import CashyTask, Intent
from ..storage.sanitizer import mask_url

if TYPE_CHECKING:
    from ..turn import Turn

HELP = """I can:
  open <bank>                         open its login page; you sign in yourself
  I'm logged in                       I check, then we're ready
  open my statements / transfer activity
  download my September 2025 statement
  open the transfer activity for <payee>
  what options do you see? · click <words> · click the second one · go back
  try another route · stop here · pause · resume
  remember how you did that           save the route as a versioned ADW
I never type, read or store passwords, and I won't press anything that pays,
transfers, submits or deletes without a "yes" from you."""


class StatusAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s, task = turn.session, turn.session.task
        bank = turn.bank.title if turn.bank else "none"
        turn.say( f"State: { s.state.replace( '_', ' ' ) }. Bank: { bank }." )
        if task.goal:
            period = f" { task.period.year }-{ task.period.month:02d}" if task.period else ""
            turn.say( f"Task: { task.goal.replace( '_', ' ' ) }{ period } -- { task.status }"
                      + ( f" ({ task.mode })" if task.mode else "" ) + ( f": { task.detail }" if task.detail else "" ))
        if s.url:
            turn.say( f"Last page: { mask_url( s.url ) }" )
        if s.trail:
            turn.say( "Route so far: " + " → ".join( t.text for t in s.trail if not t.undone ))
        if s.question:
            turn.say( f"Waiting on: { s.question.prompt.splitlines()[ 0 ]}" )


class HelpAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        turn.say( HELP )


class UnknownAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        turn.say( f"I didn't understand \"{ intent.text }\". Say 'help' to see what I can do." )
        turn.halted = True


class StopAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        if s.task.goal:
            s.task.status, s.task.detail = "completed", "stopped by you"
        s.question = None
        if s.state == "paused":
            turn.move( s.paused_from or "idle" )
        if s.state == "awaiting_user_input":
            turn.move( s.asked_from or ( "ready" if s.bank else "idle" ))
        turn.event( "completed", "stopped" )
        turn.say( "Stopped. I've left the page exactly as it is." )
        turn.halted = True


class PauseAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        if turn.session.state == "paused":
            turn.say( "I'm already paused." )
            return
        if turn.session.task.status == "running":
            turn.session.task.status = "paused"
        turn.move( "paused" )
        turn.say( "Paused. Nothing will happen until you say 'resume'." )
        turn.halted = True


class ResumeAction:
    """Back to where the session was, then carry on with the task if it was
    interrupted -- the person doesn't repeat the bank, the month or the payee."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        if s.state == "paused":
            turn.move( s.paused_from or "idle" )
        if s.question:
            turn.say( s.question.prompt )
            turn.move( "awaiting_user_input" )
            turn.halted = True
            return
        task = s.task
        if task.status not in ( "paused", "waiting", "failed", "running" ) or not task.goal:
            turn.say( "Resumed. There's no unfinished task; what would you like?" )
            return
        again = continuation( task )
        if again is None:
            turn.say( "Resumed." )
            return
        turn.say( f"Resuming: { task.goal.replace( '_', ' ' ) }." )
        turn.handle( again )


def continuation( task: CashyTask ) -> Intent | None:
    """The request that carries an unfinished task on."""
    if task.goal == "statement_download":
        return Intent( kind="get_statement", period=task.period, account=task.account, text="resume" )
    if task.goal == "transaction":
        return Intent( kind="find_transaction", target=task.subject, account=task.account, text="resume" )
    if task.goal and task.goal != "login":
        return Intent( kind="go_to", goal=task.goal, text="resume" )
    return None

"""GuidedExplorer: IExplorer -- Mode A, guided discovery.

Read the page, rank what it offers against the goal's words, press the
clear winner, read again. When two options are close it asks rather than
guesses, and it gives up after a few hops rather than wander: an
exploration stays on the bank's own pages, inside the person's session.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..banks.directory import goal_vocabulary
from ..models import ExploreOutcome, Intent, Question, RankedOption

if TYPE_CHECKING:
    from ..turn import Turn

MAX_HOPS  = 4
CLEAR_GAP = 0.5           # a winner this far ahead of the runner-up is pressed without asking


class GuidedExplorer:
    def explore( self, turn: "Turn", goal: str ) -> ExploreOutcome:
        vocabulary = goal_vocabulary( turn.bank, goal )
        if vocabulary is None:
            turn.fail( f"I don't know which page '{ goal }' is." )
            return "lost"
        turn.session.task.mode = turn.session.task.mode or "discovery"
        turn.event( "exploring", goal )
        for _ in range( MAX_HOPS ):
            if turn.services.arrival.arrived( turn.browser(), vocabulary ):
                return "arrived"
            snapshot = turn.read_page()
            if snapshot.login_form and not snapshot.signed_in:
                turn.say( "This page is asking you to sign in. Sign in yourself, then tell me when you're logged in." )
                turn.move( "awaiting_user_login" )
                turn.halted = True
                return "asked"
            here    = snapshot.url.split( "#" )[ 0 ]
            ranked  = [ r for r in turn.services.ranker.rank( snapshot.options, vocabulary.words )
                        if r.option.css not in turn.session.task.tried
                        and ( not r.option.href or r.option.href.split( "#" )[ 0 ] != here )]
            if not ranked:
                return "lost"
            if not _clear( ranked ):
                turn.ask( Question( about="choice", choices=[ r.option for r in ranked[ :3 ]],
                                    resume=Intent( kind="go_to", goal=goal ),
                                    prompt="I'm not sure which of these leads there:\n" + "\n".join(
                                        f"  { i }. { r.option.text } ({ r.why })" for i, r in enumerate( ranked[ :3 ], 1 ))
                                        + "\nWhich one? (say the number)" ))
                return "asked"
            top = ranked[ 0 ].option
            turn.session.task.tried.append( top.css )
            turn.say( f"Clicking '{ top.text }' ({ ranked[ 0 ].why })." )
            if not turn.services.clicker.press( turn, top ):
                return "asked"
        return "arrived" if turn.services.arrival.arrived( turn.browser(), vocabulary ) else "lost"


def _clear( ranked: list[ RankedOption ]) -> bool:
    return len( ranked ) == 1 or ranked[ 0 ].score - ranked[ 1 ].score >= CLEAR_GAP

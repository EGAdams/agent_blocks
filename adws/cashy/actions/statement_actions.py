"""'Download my September 2025 statement.'

Ask for the month if it is missing; skip a statement already downloaded
unless asked again; get signed in; run a learned or hand-made playback if
one exists (Mode B), else explore to the statements page and find the
month (Mode A); then check the file and write it in the ledger. Downloading
never imports anything into the books.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from browser_workflows.models import Period

from ..banks.directory import goal_vocabulary
from ..documents.folder import sha256
from ..models import CashyTask, DownloadRecord, Intent, Question
from ..page.ranker import ACTIONABLE
from .common import ensure_signed_in
from .page_actions import describe

if TYPE_CHECKING:
    from ..turn import Turn

GOAL         = "statement_download"
PERIOD_BLANK = ( "month_name", "month_abbr", "month_2", "month" )
WHOLE_PHRASE = 1.0        # below this a ranked option shares only some words ("2025"), not the month


class GetStatementAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        same   = s.task.goal == GOAL
        period = intent.period or ( s.task.period if same and ( intent.again or intent.text == "resume" ) else None )
        task   = CashyTask( goal=GOAL, period=period, status="running",
                            account=intent.account or ( s.task.account if same else "" ))
        if period is None:
            s.task = task.model_copy( update={ "status": "waiting" })
            turn.ask( Question( about="period", prompt="Which month and year would you like?", resume=intent ))
            return
        if not ensure_signed_in( turn, intent, task ):
            return
        if s.task.goal not in ( "statements", GOAL ):
            s.trail, s.trail_start = [], ""             # a new route starts here
        s.task = task
        bank   = turn.bank
        key    = f"{ period.year }-{ period.month:02d}"
        if not intent.again:
            have = turn.services.ledger.find( bank.key, task.account, key )
            if have and Path( have.path ).is_file():
                turn.say( f"I already downloaded the { bank.title } statement for { key } ({ have.at[ :10 ]}): "
                          f"{ have.path }. Say 'download it again' if you want a fresh copy." )
                task.status = "completed"
                return
        before = turn.services.watcher.before()
        workflow = turn.services.workflows.resolve( bank.key, GOAL )
        if workflow and ( workflow.source == "hand-made" or any( n in PERIOD_BLANK for n in workflow.version.needs )):
            task.mode = "playback"
            result = turn.services.playback.run( turn, workflow, period )
            files  = turn.services.watcher.wait_new( before, 30.0 ) if result.ok else []
            if files:
                turn.say( f"Used the saved workflow '{ workflow.playback }'." )
                _finish( turn, files[ 0 ], key )
                return
            observed = result.message if not result.ok else "it finished, but no file arrived"
            case = turn.services.reporter.report( turn, f"'{ workflow.playback }' downloads { key }", observed )
            turn.say( f"The saved workflow didn't do it ({ observed }). I've written it up for the Mechanic "
                      f"({ case }); I'll find the way step by step and keep the old version as it is." )
            before = turn.services.watcher.before()
        task.mode = "discovery"
        turn.prepare_downloads()
        outcome = turn.services.explorer.explore( turn, "statements" )
        if outcome == "lost":
            task.status, task.detail = "failed", "no route to the statements page"
            turn.fail( "I couldn't find the statements page from here. Say 'what options do you see?' and tell me "
                       "which to click; I'll carry on from there when you say 'resume'." )
            return
        if outcome == "asked":
            task.status = "waiting"
            return
        _download( turn, period, key, before )


def _download( turn: "Turn", period: Period, key: str, before: set[ str ]) -> None:
    services, task = turn.services, turn.session.task
    page   = turn.read_page()
    ranked = [ r for r in services.ranker.rank( page.options, services.statements.words( period ))
               if r.score >= WHOLE_PHRASE ]
    if not ranked:
        task.status, task.detail = "failed", f"no statement for { key } listed"
        turn.say( f"I'm on the statements page but don't see one for { _said( turn, period ) }.", *describe( page, 12 ))
        turn.fail( "It may be under another year or account. Tell me what to click, then say 'resume'." )
        return
    if len( ranked ) > 1 and ranked[ 0 ].score - ranked[ 1 ].score < 0.5:
        choices = [ r.option for r in ranked[ :4 ]]
        task.status = "waiting"
        turn.ask( Question( about="choice", choices=choices,
                            resume=Intent( kind="get_statement", period=period, text="resume" ),
                            prompt=f"Several things mention { _said( turn, period ) }:\n"
                                   + "\n".join( f"  { i }. { o.text }" for i, o in enumerate( choices, 1 ))
                                   + "\nWhich one is the statement? (say the number)" ))
        return
    turn.event( "downloading", key )
    turn.say( f"Clicking '{ ranked[ 0 ].option.text }'." )
    if not services.clicker.press( turn, ranked[ 0 ].option ):
        return
    files = services.watcher.wait_new( before, 12.0 )
    if not files:
        files = _press_download( turn, before )
    if not files:
        task.status, task.detail = "failed", "no file arrived"
        turn.fail( f"I clicked it, but no file arrived in { services.download_directory() }. "
                   "Say 'what options do you see?' to look for a download button." )
        return
    _finish( turn, files[ 0 ], key )


def _press_download( turn: "Turn", before: set[ str ]) -> list[ Path ]:
    """The month opened a page, not a file: press its download button."""
    words  = goal_vocabulary( turn.bank, "download" ).words
    page   = turn.read_page()
    ranked = [ r for r in turn.services.ranker.rank( page.options, words )
               if r.score >= WHOLE_PHRASE and r.option.kind in ACTIONABLE ]
    if not ranked:
        return []
    turn.say( f"Clicking '{ ranked[ 0 ].option.text }'." )
    if not turn.services.clicker.press( turn, ranked[ 0 ].option ):
        return []
    return turn.services.watcher.wait_new( before, 30.0 )


def _finish( turn: "Turn", path: Path, key: str ) -> None:
    services, task = turn.services, turn.session.task
    problem = services.verifier.problem( path )
    if problem:
        task.status, task.detail = "failed", problem
        turn.fail( f"A file arrived but { problem }, so I haven't counted it." )
        return
    record = DownloadRecord( bank=turn.session.bank, account=task.account, period=key, path=str( path ),
                             size=path.stat().st_size, sha256=sha256( path ))
    services.ledger.add( record )
    task.status, task.detail = "completed", str( path )
    turn.event( "completed", f"downloaded { path.name }" )
    turn.say( f"Downloaded { path.name } ({ record.size:,} bytes): { path }",
              "I haven't imported it into the books -- that's a separate step, only if you ask." )
    if task.mode == "discovery":
        turn.say( "Say 'remember how you did that' and I'll save the route for next time." )


def _said( turn: "Turn", period: Period ) -> str:
    return turn.services.statements.words( period )[ 0 ]

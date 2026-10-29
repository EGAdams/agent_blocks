"""'Open the Transfer Activity for the Housing Gas Bill.'

The person's own transfer records suggest which payee and account that
means; the person confirms it; Cashy asks once which bank holds the
account and remembers; then it opens that bank's transfer activity and
shows the rows that mention the payee. It opens and changes nothing:
"open Transfer Activity" never means "make a transfer".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import CashyTask, Intent, Question
from ..page.ranker import tokens
from .common import ensure_signed_in
from .page_actions import arrive

if TYPE_CHECKING:
    from ..turn import Turn

GOAL = "transaction"
ROWS = 8


class FindTransactionAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s       = turn.session
        subject = intent.target or s.task.subject
        account = intent.account or ( s.task.account if s.task.goal == GOAL and intent.text == "resume" else "" )
        task    = CashyTask( goal=GOAL, subject=subject, account=account, status="running" )
        if not account:
            self._suggest( turn, subject, task )
            return
        bank = intent.bank or turn.services.accounts.bank_for( account )
        if not bank:
            s.task = task.model_copy( update={ "status": "waiting" })
            turn.ask( Question( about="account", prompt=f"Which bank holds the account ending { account }?",
                                resume=Intent( kind="find_transaction", target=subject, account=account, text="resume" )))
            return
        if not ensure_signed_in( turn, intent.model_copy( update={ "bank": bank }), task ):
            return
        s.task = task
        if not arrive( turn, "transfer_activity" ):
            return
        self._show_rows( turn, subject, account )

    def _suggest( self, turn: "Turn", subject: str, task: CashyTask ) -> None:
        hints = turn.services.transfers.candidates( subject )
        if not hints:
            turn.say( f"Your transfer records have nothing like '{ subject }'. Say 'open transfer activity' to look "
                      "on the bank's site yourself." )
            turn.halted = True
            return
        exact = any( h.payee.casefold() == subject.casefold() for h in hints )
        lines = [] if exact else [ f"Your transfer records have no payee called '{ subject }'." ]
        lines.append( "Is it one of these?" )
        for i, h in enumerate( hints, 1 ):
            accounts = ", ".join( h.accounts ) or "an unnamed account"
            lines.append( f"  { i }. '{ h.payee }' -- { h.count } transfer(s) from { accounts }, "
                          f"latest { h.last_date } for { h.last_amount }" )
        lines.append( "Say the number, or 'no'." )
        turn.session.task = task.model_copy( update={ "status": "waiting" })
        turn.ask( Question( about="transaction", hints=hints, prompt="\n".join( lines ),
                            resume=Intent( kind="find_transaction", target=subject, text="resume" )))

    def _show_rows( self, turn: "Turn", payee: str, account: str ) -> None:
        wanted = tokens( payee )
        last4  = account.lstrip( "•" )
        rows   = [ line.strip() for line in turn.browser().body_text().splitlines()
                   if line.strip() and wanted <= tokens( line )]
        task = turn.session.task
        if not rows:
            task.status, task.detail = "completed", "no row on the page"
            turn.say( f"Nothing on this page mentions '{ payee }'. It may be in another date range; tell me what to "
                      "click and I'll look there." )
            return
        turn.say( f"{ len( rows ) } row(s) here mention '{ payee }':" )
        for row in rows[ :ROWS ]:
            turn.say( f"  - { row }" + ( f"  [from { account }]" if last4 and last4 in row else "" ))
        if not any( last4 in r for r in rows ):
            turn.say( f"None of them names the account ending { account }, so check the account before trusting a match." )
        turn.say( "I've only looked; nothing was changed, and I won't start a transfer or a payment from here." )
        task.status = "completed"

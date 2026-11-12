"""Opening a bank and waiting for the person to sign in."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from browser_workflows.errors import BrowserWorkflowError

from ..models import CashyTask, Intent, Question
from .session_actions import continuation

if TYPE_CHECKING:
    from ..turn import Turn


class OpenBankAction:
    """A login ADW if one is registered, else the profile's own login URL --
    never a search result. Cashy then waits; the person signs in."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        bank = turn.services.banks.by_key( intent.bank or turn.session.bank )
        if bank is None:
            turn.ask( Question( about="bank", prompt="Which bank? (" + ", ".join(
                b.title for b in turn.services.banks.all()) + ")", resume=intent ))
            return
        s = turn.session
        if s.bank != bank.key:
            s.target_id = ""
        s.bank = bank.key
        waiting = s.task if s.task.status == "waiting" else None
        s.trail, s.trail_start = [], ""
        login = turn.services.workflows.resolve( bank.key, "login" )
        if login:
            turn.say( f"Running the saved login route '{ login.playback }'." )
            result = turn.services.playback.run( turn, login, None )
            if not result.ok:
                turn.say( f"It didn't finish ({ result.message }); opening the login page directly instead." )
                self._open( turn, bank.login_url, bank.hosts )
        else:
            self._open( turn, bank.login_url, bank.hosts )
        page = turn.read_page()
        if page.signed_in:
            turn.say( f"You're already signed in to { bank.title }." )
            turn.move( "ready" )
            if waiting:
                s.task = waiting
                turn.handle( continuation( waiting ))
            return
        s.task = waiting or CashyTask( goal="login", status="waiting" )
        turn.move( "awaiting_user_login" )
        turn.say( f"The { bank.title } login page is ready. Please sign in yourself -- I don't see or keep "
                  "anything you type. Tell me when you're logged in." )
        turn.halted = True

    def _open( self, turn: "Turn", url: str, hosts: list[ str ]) -> None:
        browser = turn.browser()
        turn.event( "navigating", url )
        if any( h in browser.current_url for h in hosts ):
            browser.navigate( url )
        else:
            browser.open_tab( url )                     # the person's own tab is left alone
        turn.session.target_id = browser.target_id
        landed( browser, hosts )


#: how long a bank's login page may spend redirecting (Fifth Third: www.53.com -> "Login Redirect" -> secure.53.com)
LANDING_SECONDS = 20.0


def on_hosts( url: str, hosts: list[ str ]) -> bool:
    """The URL's own host is one of the bank's, or a subdomain of one -- not merely a string containing it."""
    try:
        host = ( urlsplit( url ).hostname or "" ).lower()
    except ValueError:
        return False
    return any( host == h.lower() or host.endswith( "." + h.lower()) for h in hosts )


def landed( browser, hosts: list[ str ], seconds: float = LANDING_SECONDS, sleep=time.sleep ) -> str:
    """Until the tab is on the bank's own domain; "the login page is ready" is never said of another page.
    2026-11-11, R46: Fifth Third's redirect page reads about:blank, and Cashy reported ready from it."""
    waited = 0.0
    while True:
        url = browser.current_url
        if on_hosts( url, hosts ):
            return url
        if waited >= seconds:
            shown = urlsplit( url ).hostname or url.split( "?" )[ 0 ][ :60 ] or "nothing"
            raise BrowserWorkflowError( f"the tab did not reach the bank's own site ({ ', '.join( hosts ) }) "
                                        f"within { round( seconds ) }s; it shows { shown }" )
        sleep( 0.5 )
        waited += 0.5


class LoggedInAction:
    """'I'm logged in' is checked against the page, not taken on trust --
    unless the page gives no sign either way."""

    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        page = turn.read_page()
        if not s.bank:
            found = turn.services.banks.for_url( page.url )
            if found is None:
                turn.ask( Question( about="bank", prompt="Which bank are you signed in to?", resume=intent ))
                return
            s.bank = found.key
        bank = turn.bank
        if page.signed_in is False:
            turn.say( f"I still see a sign-in form on { bank.title }'s page. Tell me again once you're in." )
            turn.move( "awaiting_user_login" )
            turn.halted = True
            return
        turn.move( "ready" )
        s.trail, s.trail_start = [], page.url
        turn.say( f"You're signed in to { bank.title }." if page.signed_in else
                  f"I can't tell from the page whether you're signed in, so I'll take your word for it." )
        if s.task.status == "waiting" and s.task.goal not in ( "", "login" ):
            turn.say( f"Carrying on with { s.task.goal.replace( '_', ' ' ) }." )
            turn.handle( continuation( s.task ))
        elif s.task.goal == "login":
            s.task.status = "completed"

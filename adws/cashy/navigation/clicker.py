"""GuardedOptionClicker: IOptionClicker.

Every click Cashy makes goes through here, so there is one place that
weighs risk: a consequential option is never pressed without a "yes" from
the person at the keyboard -- an agent's "yes" does not count.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import LocatorCandidate

from ..interfaces.browser import ICashyBrowser
from ..models import Intent, NavigationStep, PageOption, Question

if TYPE_CHECKING:
    from ..turn import Turn

SETTLE_SECONDS = 12.0
QUIET_SECONDS  = 1.5      # a click that stays on the page (a download) settles after this


class GuardedOptionClicker:
    def press( self, turn: "Turn", option: PageOption, approved: bool = False ) -> bool:
        risk = turn.services.risk.assess( option )
        if risk.consequential and not approved:
            if turn.actor != "user":
                turn.fail( f"'{ option.text }': { risk.reason }. Only the person at the keyboard can approve that, "
                           "so I've stopped here." )
                return False
            turn.ask( Question( about="approval", choices=[ option ],
                                resume=Intent( kind="click", target=option.text ),
                                prompt=f"'{ option.text }': { risk.reason }. Pressing it may do something that can't "
                                       "be undone. Shall I press it? (yes / no)" ))
            return False
        browser = turn.browser()
        element, resolution = turn.services.elements.resolve( browser, _locators( option ))
        if element is None:
            tried = "; ".join( f"{ a.label }: { a.outcome }" for a in resolution.attempts )
            raise BrowserWorkflowError( f"I can no longer find '{ option.text }' on the page ({ tried })" )
        before = browser.current_url
        if not turn.session.trail:
            turn.session.trail_start = before
        turn.event( "navigating", f"clicking '{ option.text }'" )
        element.click()
        settle( browser, before )
        turn.session.trail.append( NavigationStep( action="click", text=option.text, css=option.css, tag=option.tag,
                                                   label=option.label, url_before=before,
                                                   url_after=_url( browser )))
        return True


def settle( browser: ICashyBrowser, before: str ) -> None:
    """Until a new page has loaded, or the old one has stayed quiet a moment."""
    start = time.monotonic()
    time.sleep( 0.25 )
    while time.monotonic() - start < SETTLE_SECONDS:
        url = _url( browser )
        complete = browser.ready_state() == "complete"
        if complete and ( url and url != before or time.monotonic() - start > QUIET_SECONDS ):
            return
        time.sleep( 0.2 )


def _url( browser: ICashyBrowser ) -> str:
    try:
        return browser.current_url
    except BrowserWorkflowError:
        return ""                                       # mid-navigation


def _locators( option: PageOption ) -> list[ LocatorCandidate ]:
    found = []
    if option.css:
        found.append( LocatorCandidate( strategy="css", value=option.css, tag=option.tag, source="css", rank=20 ))
    if option.label:
        found.append( LocatorCandidate( strategy="label", value=option.label, tag=option.tag, source="aria_label", rank=30 ))
    if option.text:
        found.append( LocatorCandidate( strategy="text", value=option.text, tag=option.tag, source="text", rank=40 ))
    return found

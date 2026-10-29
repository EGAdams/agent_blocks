"""What several commands share: getting to a signed-in bank, finding an
option again on a page that has been re-read, and saying what is on it."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import CashyTask, Intent, PageOption, PageSnapshot, Question
from ..page.ranker import ACTIONABLE
from ..storage.sanitizer import mask_text

if TYPE_CHECKING:
    from ..turn import Turn


def ensure_signed_in( turn: "Turn", intent: Intent, task: CashyTask ) -> bool:
    """True when the session is signed in to the bank the request is about.
    Otherwise it opens that bank or asks, leaving `task` waiting, and the
    task carries on by itself once the person says they're logged in."""
    s = turn.session
    wanted = intent.bank or s.bank
    if not wanted:
        turn.ask( Question( about="bank", prompt="Which bank? (" + ", ".join(
            b.title for b in turn.services.banks.all()) + ")", resume=intent ))
        return False
    if wanted != s.bank or s.state == "idle":
        s.task = task.model_copy( update={ "status": "waiting" })
        turn.run( Intent( kind="open_bank", bank=wanted, text=intent.text ))
        return False
    if s.state == "awaiting_user_login":
        page = turn.read_page()
        if not page.signed_in:
            s.task = task.model_copy( update={ "status": "waiting" })
            turn.say( f"Please sign in to { turn.bank.title } first; I'll carry on when you tell me you're logged in." )
            turn.halted = True
            return False
        turn.move( "ready" )
        s.trail, s.trail_start = [], page.url
    return True


def same_option( page: PageSnapshot, old: PageOption ) -> PageOption | None:
    """`old` (possibly masked when it was saved) on the page as it is now."""
    for o in page.options:
        if o.kind == old.kind and mask_text( o.text ) == old.text and ( o.css == old.css or mask_text( o.css ) == old.css ):
            return o
    for o in page.options:
        if o.kind == old.kind and mask_text( o.text ) == old.text:
            return o
    return None


def describe( page: PageSnapshot, limit: int = 20 ) -> list[ str ]:
    shown = [ o for o in page.options if o.kind in ACTIONABLE ][ :limit ]
    lines = [ f"'{ page.title or page.url }' offers:" ]
    lines += [ f"  { o.number }. { mask_text( o.text ) } ({ o.kind })" for o in shown ]
    more = len([ o for o in page.options if o.kind in ACTIONABLE ]) - len( shown )
    if more > 0:
        lines.append( f"  ...and { more } more." )
    return lines


def back_to_rest( turn: "Turn" ) -> None:
    """Close the open question and return to the state it was asked from."""
    s = turn.session
    s.question = None
    if s.state == "awaiting_user_input":
        turn.move( s.asked_from or ( "ready" if s.bank else "idle" ))


def pick( items: list, number: int ):
    if number == -1 and items:
        return items[ -1 ]
    return items[ number - 1 ] if 1 <= number <= len( items ) else None

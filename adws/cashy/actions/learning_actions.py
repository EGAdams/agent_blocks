"""'Remember how you did that' / 'Save this navigation as an ADW'."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Intent

if TYPE_CHECKING:
    from ..turn import Turn


class RememberAction:
    def run( self, intent: Intent, turn: "Turn" ) -> None:
        s = turn.session
        turn.event( "learning", s.task.goal )
        period  = s.task.period if s.task.goal == "statement_download" else None
        version = turn.services.saver.save( s, period )
        bank    = turn.bank.title if turn.bank else s.bank
        turn.say( f"Saved as '{ version.playback }': version { version.version } of { bank } "
                  f"{ version.goal.replace( '_', ' ' ) }. It stays a candidate until a run of it succeeds." )
        if version.needs:
            turn.say( f"It takes { ', '.join( version.needs ) }, so it works for any month." )
        if version.note:
            turn.say( f"({ version.note })" )
        if not turn.services.memory.available:
            turn.say( "(LanceDB isn't installed in this interpreter, so I skipped the memory pointer; the workflow "
                      "itself is saved and I'll still find it.)" )

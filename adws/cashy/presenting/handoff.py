"""'Put Toyota on', 'bring up Sydney': the hand-off back to another desk.

Only the dashboard's agent windows are named here, and a sentence must ask
for one by a verb as well as by name, so "Toyota said to open Chase" stays
Cashy's.
"""
from __future__ import annotations

import re

from receptionist_team.adapters.handoff_model import HANDOFF_AGENTS

from ..models import DeskHandoff

#: The windows the dashboard can open (one list, which it mirrors), minus Cashy herself.
DESK_AGENTS: dict[ str, str ] = { k: v for k, v in HANDOFF_AGENTS.items() if k != "cashy" }

_ASK = re.compile( r"\b(put|bring( up| back)?|get|open|switch( me)?( over)? to|back to|go back to|hand( me)?( over)? to|"
                   r"let me (talk|speak) to|i want( to talk to)?|transfer( me)? to)\b" )


class WordHandoffDetector:
    def __init__( self, agents: dict[ str, str ] | None = None ) -> None:
        self._agents = dict( agents or DESK_AGENTS )

    def detect( self, text: str ) -> DeskHandoff | None:
        words = text.lower()
        if not _ASK.search( words ):
            return None
        for key, name in self._agents.items():
            if re.search( rf"\b{ key }\b", words ):
                return DeskHandoff( agent=key, name=name )
        return None

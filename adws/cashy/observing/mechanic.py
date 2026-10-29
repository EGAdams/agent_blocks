"""CaseFileReporter: IFailureReporter -- a failure written up for the Mechanic.

The case says what was expected and what was seen, where (a masked URL),
the words of what the page offered, and the trail -- never a value, never
a picture. The Mechanic proposes; nothing here changes a workflow.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..storage.sanitizer import mask_text, mask_url

if TYPE_CHECKING:
    from ..turn import Turn


class CaseFileReporter:
    def __init__( self, directory: Path ) -> None:
        self._directory = Path( directory )

    def report( self, turn: "Turn", expected: str, observed: str ) -> str:
        session = turn.session
        case = {
            "bank":     session.bank,
            "goal":     session.task.goal,
            "mode":     session.task.mode,
            "expected": mask_text( expected ),
            "observed": mask_text( observed ),
            "url":      mask_url( session.url ),
            "offered":  [ mask_text( o.text ) for o in session.options[ :40 ]],
            "trail":    [ mask_text( s.text ) for s in session.trail ],
            "boundary": "read-only: no transfer, payment or change may be proposed as a repair",
        }
        self._directory.mkdir( parents=True, exist_ok=True )
        stamp = datetime.now( timezone.utc ).strftime( "%Y%m%dT%H%M%S%f" )
        path  = self._directory / f"case_{ stamp }_{ session.bank or 'none' }.json"
        path.write_text( json.dumps( case, indent=2 ), encoding="utf-8" )
        return str( path )

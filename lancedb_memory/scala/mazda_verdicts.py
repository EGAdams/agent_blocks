"""IMazdaVerdicts over the dashboard's `recent_report.json`, read-only.

"Mazda first": the dashboard runs Mazda's intake classifier (rol_finances'
`tools/mazda_intake.py`) before it stages a scan into `incoming_scans/`, and
records the verdict against the staged path in this file *before* Mazda's
turn starts. Scala reads it and stays out of anything Mazda claimed.

When the vision check calls a page non-financial (a tax form, "other") the
dashboard skips Mazda's turn and records the intake with a detail starting
LEFT_FOR_SCALA; `left_for_scala` reports that, so a page Scala's own rules
would hand back to Mazda goes to the inbox instead of to nobody.

The file holds only the newest intake per scanner, so a verdict can be
overwritten by the next scan from the same scanner. Scala asks about a scan
seconds after it is staged and scans are minutes apart, but when the record
is gone the answer is None and Scala's own rules -- which also know receipts
and statements -- decide instead. This module never writes the file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: Mazda's document kinds. Anything else ("unknown", None) is up for grabs.
MAZDA_KINDS = frozenset({ "receipt", "statement", "bank_statement" })
#: How the dashboard's intake/scala_handoff.py starts the detail of a skipped turn.
LEFT_FOR_SCALA = "Left for Scala:"


def default_record() -> Path:
    return Path( os.environ.get( "SCALA_MAZDA_RECORD",
                                 Path.home() / "letta-code" / "dashboard" / "recent_report.json" ))


class RecentReportVerdicts:
    def __init__( self, record: Path | None = None ) -> None:
        self._record = record or default_record()

    def doc_kind( self, image: Path ) -> str | None:
        for intake in self._intakes( image ):
            facade = intake.get( "facade" ) if isinstance( intake.get( "facade" ), dict ) else {}
            kind = intake.get( "doc_kind" ) or facade.get( "doc_kind" )
            if kind:
                return str( kind )
        return None

    def left_for_scala( self, image: Path ) -> bool:
        return any( str( intake.get( "status_detail" ) or "" ).startswith( LEFT_FOR_SCALA )
                    for intake in self._intakes( image ))

    def _intakes( self, image: Path ) -> list[ dict ]:
        try:
            data = json.loads( self._record.read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            return []
        if not isinstance( data, dict ):
            return []
        intakes = list(( data.get( "scanner_intakes" ) or {} ).values()) + [ data.get( "intake" )]
        return [ intake for intake in intakes
                 if isinstance( intake, dict ) and intake.get( "image_path" ) == str( image )]

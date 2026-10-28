"""FileAlertBoard -- IAlertBoard as one JSON file per alert, per caller.

    alerts/<agent>/pending/<task id>.json        raised, not yet heard
    alerts/<agent>/acknowledged/<task id>.json   heard; kept for the record

Writes go through a temp file and `os.replace`, the same atomic move the
mailboxes use, so a reader never sees half an alert. Raising the same task's
alert twice overwrites it rather than duplicating it.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..models import Alert


class FileAlertBoard:

    def __init__( self, root: Path ) -> None:
        self._root = Path( root )

    def raise_alert( self, alert: Alert ) -> None:
        folder = self._dir( alert.agent, "pending" )
        tmp    = folder / f".{ alert.id }.tmp"
        tmp.write_text( alert.model_dump_json( indent=2 ), encoding="utf-8" )
        os.replace( tmp, folder / f"{ alert.id }.json" )

    def pending( self, agent: str ) -> list[ Alert ]:
        alerts = []
        for path in sorted( self._dir( agent, "pending" ).glob( "*.json" )):
            try:
                alerts.append( Alert.model_validate_json( path.read_text( encoding="utf-8" )))
            except ( OSError, ValueError ):
                continue   # acknowledged between the glob and the read
        return sorted( alerts, key=lambda a: a.raised_at )

    def acknowledge( self, agent: str, alert_ids: list[ str ] | None = None ) -> int:
        done   = 0
        wanted = None if alert_ids is None else set( alert_ids )
        target = self._dir( agent, "acknowledged" )
        for path in self._dir( agent, "pending" ).glob( "*.json" ):
            if wanted is not None and path.stem not in wanted:
                continue
            try:
                os.replace( path, target / path.name )
                done += 1
            except FileNotFoundError:
                continue   # somebody else acknowledged it first
        return done

    def _dir( self, agent: str, box: str ) -> Path:
        folder = self._root / agent / box
        folder.mkdir( parents=True, exist_ok=True )
        return folder

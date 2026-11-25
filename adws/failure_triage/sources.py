"""IFailureSource adapters. Read-only: files are opened for reading and nothing else."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Failure


def _when( value: str | None, fallback: float ) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat( value.replace( "Z", "+00:00" ))
            return parsed if parsed.tzinfo else parsed.replace( tzinfo=timezone.utc )
        except ValueError:
            pass
    return datetime.fromtimestamp( fallback, tz=timezone.utc )


class FailedMailboxSource:
    """Every request in `mailboxes/<agent>/inbox/failed/`. The envelope carries agent, error and request,
    which is everything triage needs; the mechanic's cases and the Bridge supervisor state carry less."""
    name = "failed-mailboxes"

    def __init__( self, mailboxes: Path ) -> None:
        self._root = mailboxes
        self.unreadable: list[ str ] = []

    def read( self ) -> list[ Failure ]:
        self.unreadable = []
        found: list[ Failure ] = []
        for path in sorted( self._root.glob( "*/inbox/failed/*.json" )):
            try:
                data = json.loads( path.read_text())
                if data.get( "kind", "request" ) != "request":
                    continue
                found.append( Failure(
                    task_id=str( data[ "id" ]), agent=str( data[ "recipient" ]), sender=str( data.get( "sender", "" )),
                    task_type=str( data.get( "task_type", "" )), error=str( data.get( "error" ) or "(no error recorded)" ),
                    failed_at=_when( data.get( "completed_at" ), path.stat().st_mtime ),
                    request=str( data.get( "text", "" ))[ :200 ],
                    bridge_task_id=str(( data.get( "context" ) or {}).get( "bridge_task_id", "" ))))
            except ( OSError, ValueError, KeyError ) as exc:
                self.unreadable.append( f"{ path.name }: { type( exc ).__name__ }" )
        return found

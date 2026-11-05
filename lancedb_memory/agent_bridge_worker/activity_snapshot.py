"""The dashboard's Worker Activity snapshot, read from this machine's post office.

Every Bridge task the worker runs is handed to an agent through the post office
as a request from `chatgpt-bridge` carrying `context.bridge_task_id`. Those
envelopes are the only task ledger on this machine, so this module turns them
into the JSON array the dashboard reads through `WORKER_ACTIVITY_SNAPSHOT`
(`letta-code/dashboard/docs/worker-activity.md`).

Read-only: it never moves, claims or edits an envelope, and never talks to the
gateway. The prompt text and the result are left out, as in the gateway's own
client view. What it cannot see, it does not invent: a task still queued at the
gateway, or one refused before it reached the post office, has no row.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.activity_snapshot          # write it once
    .venv/bin/python -m agent_bridge_worker.activity_snapshot --print  # show it, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 1 ]))

from agent_bridge_worker.redaction import SecretScanner  # noqa: E402
from agent_comms.models import Envelope, MessageKind, TaskStatus  # noqa: E402

SENDER          = "chatgpt-bridge"
WORKSPACE_ROOT  = Path( __file__ ).resolve().parents[ 2 ]
MAILBOX_ROOT    = WORKSPACE_ROOT / "adws" / "adw_data" / "post_office" / "mailboxes"
WORKSPACES_FILE = Path( __file__ ).resolve().parent / "workspaces.json"
SNAPSHOT_PATH   = Path( os.path.expanduser( "~/.cache/agent_bridge/worker_activity.json" ))
MAX_ROWS        = 50
ERROR_CLIP      = 240
OPEN_STATES     = { TaskStatus.QUEUED, TaskStatus.RUNNING }


class WorkerTaskRow( BaseModel ):
    """One row of the snapshot; the field names are the dashboard's."""

    model_config = ConfigDict( extra="forbid" )

    id:           str
    agent:        str
    operation:    str
    workspace:    str
    status:       str
    queue_age:    str | None
    run_duration: str | None
    attempts:     int
    last_update:  str
    error:        str | None


def workspace_names( path: Path ) -> dict[ str, str ]:
    """Directory -> workspace name, from the worker's own workspaces.json."""
    try:
        config = json.loads( path.read_text( encoding="utf-8" ))
    except ( OSError, ValueError ):
        return {}
    return { str( Path( spec[ "path" ])): name for name, spec in config.get( "workspaces", {}).items()
             if isinstance( spec, dict ) and "path" in spec }


def duration( start: datetime | None, end: datetime | None ) -> str | None:
    if start is None or end is None:
        return None
    seconds = max( 0, int(( end - start ).total_seconds()))
    if seconds < 60:
        return f"{ seconds }s"
    if seconds < 3600:
        return f"{ seconds // 60 }m { str( seconds % 60 ).zfill( 2 )}s"
    return f"{ seconds // 3600 }h { str( seconds % 3600 // 60 ).zfill( 2 )}m"


def safe_error( text: str | None, scanner: SecretScanner ) -> str | None:
    """Clipped, and withheld entirely if it looks like it carries a credential."""
    if not text:
        return None
    if scanner.found( text ):
        return "(error text withheld: it looked like it contained a credential)"
    flat = " ".join( text.split())
    return flat if len( flat ) <= ERROR_CLIP else flat[ :ERROR_CLIP - 1 ] + "…"


def bridge_requests( root: Path ) -> list[ Envelope ]:
    """Every request from the bridge in any agent's inbox. Unreadable files are skipped."""
    found: list[ Envelope ] = []
    for path in sorted( root.glob( "*/inbox/*/*.json" )):
        try:
            envelope = Envelope.model_validate_json( path.read_text( encoding="utf-8" ))
        except ( OSError, ValidationError, ValueError ):
            continue
        if envelope.kind == MessageKind.REQUEST and envelope.sender == SENDER and envelope.context.get( "bridge_task_id" ):
            found.append( envelope )
    return found


def row_for( envelope: Envelope, names: dict[ str, str ], now: datetime, scanner: SecretScanner ) -> WorkerTaskRow:
    status  = TaskStatus( envelope.status )
    queued  = envelope.enqueued_at or envelope.created_at
    started = envelope.dequeued_at
    ended   = envelope.completed_at
    workdir = str( envelope.context.get( "workdir" ) or "" )
    latest  = max( t for t in ( envelope.created_at, envelope.enqueued_at, started, ended ) if t is not None )
    return WorkerTaskRow(
        id=str( envelope.context[ "bridge_task_id" ]),
        agent=envelope.recipient,
        operation=envelope.task_type,
        workspace=names.get( workdir, Path( workdir ).name if workdir else "—" ),
        status=status.value,
        queue_age=duration( queued, started or ( now if status == TaskStatus.QUEUED else None )),
        run_duration=duration( started, ended or ( now if status == TaskStatus.RUNNING else None )),
        attempts=envelope.attempts,
        last_update=latest.astimezone().isoformat( timespec="seconds" ),
        error=safe_error( envelope.error, scanner ),
    )


def build_rows( root: Path = MAILBOX_ROOT, workspaces_file: Path = WORKSPACES_FILE,
                now: datetime | None = None, limit: int = MAX_ROWS ) -> list[ WorkerTaskRow ]:
    """Open work first, then the most recently touched. One row per bridge task."""
    now     = now or datetime.now( timezone.utc )
    names   = workspace_names( workspaces_file )
    scanner = SecretScanner()
    latest: dict[ str, Envelope ] = {}
    for envelope in bridge_requests( root ):
        key = str( envelope.context[ "bridge_task_id" ])
        if key not in latest or envelope.created_at > latest[ key ].created_at:
            latest[ key ] = envelope
    rows = [ row_for( e, names, now, scanner ) for e in latest.values()]
    rows.sort( key=lambda r: r.last_update, reverse=True )
    rows.sort( key=lambda r: r.status not in { s.value for s in OPEN_STATES })
    return rows[ :limit ]


def write_snapshot( rows: list[ WorkerTaskRow ], path: Path = SNAPSHOT_PATH ) -> Path:
    """Atomic: the dashboard sees the old snapshot or the new one, never half of one."""
    path.parent.mkdir( parents=True, exist_ok=True )
    text = json.dumps([ r.model_dump() for r in rows ], indent=1 )
    fd, tmp = tempfile.mkstemp( dir=path.parent, prefix=".worker_activity_" )
    try:
        with os.fdopen( fd, "w", encoding="utf-8" ) as out:
            out.write( text )
        os.chmod( tmp, 0o600 )
        os.replace( tmp, path )
    except BaseException:
        Path( tmp ).unlink( missing_ok=True )
        raise
    return path


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description=__doc__.splitlines()[ 0 ])
    parser.add_argument( "--out", type=Path, default=SNAPSHOT_PATH )
    parser.add_argument( "--print", action="store_true", help="print the rows instead of writing them" )
    args = parser.parse_args( argv )
    rows = build_rows()
    if args.print:
        print( json.dumps([ r.model_dump() for r in rows ], indent=1 ))
        return 0
    write_snapshot( rows, args.out )
    print( f"{ len( rows )} rows -> { args.out }" )
    return 0


if __name__ == "__main__":
    raise SystemExit( main())

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

Each row also carries the task's own events from the post office's event log
(`events.jsonl`): its lifecycle (sent, claimed, executing, executed, replied)
and, for an agent that reports its steps (Claude), one `progress` event per
tool call it actually made. Only recorded events are listed, never inferred
ones; their details are dropped except a progress step's one message, which
is written to be shown. `queued_at` / `started_at` / `completed_at` are the
envelope's own times, absent when the envelope has none.

The `notes` agent runs self-contained, with no post-office envelope, so its
rows come from the Notes activity log instead (`NOTES_ACTIVITY_LOG`, default
`~/.cache/agent_bridge/notes_activity.jsonl`), which both the dashboard's
Notes interface and the agent append to: reads, writes, conflicts,
authorization failures, refusals and read-back verification. Events carrying
a Bridge task id make one row per task; an event without one (a script, or a
caller refused before it said who it was) is a row of its own. The log holds
names and revisions only, never note text.

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
from agent_comms.adapters.event_log import JsonlEventLog  # noqa: E402
from agent_comms.models import Envelope, MessageKind, TaskEvent, TaskStatus  # noqa: E402

SENDER          = "chatgpt-bridge"
WORKSPACE_ROOT  = Path( __file__ ).resolve().parents[ 2 ]
MAILBOX_ROOT    = WORKSPACE_ROOT / "adws" / "adw_data" / "post_office" / "mailboxes"
WORKSPACES_FILE = Path( __file__ ).resolve().parent / "workspaces.json"
SNAPSHOT_PATH   = Path( os.path.expanduser( "~/.cache/agent_bridge/worker_activity.json" ))
MAX_ROWS        = 50
MAX_EVENTS      = 50
PROGRESS        = "task.progress"
ERROR_CLIP      = 240
OPEN_STATES     = { TaskStatus.QUEUED, TaskStatus.RUNNING }
NOTES_LOG       = Path( os.environ.get( "NOTES_ACTIVITY_LOG" ) or os.path.expanduser( "~/.cache/agent_bridge/notes_activity.jsonl" ))
NOTES_TAIL      = 2000
NOTES_OK        = { "read", "list", "write", "verified", "unchanged", "create", "rename", "trash", "restore", "purge",
                    "trash_list", "highlights", "export_docx", "email_draft" }
NOTES_FAILED    = { "conflict", "verify_failed", "error", "auth_failed", "not_approved", "refused", "protected" }
LIFECYCLE       = { "sent", "queued", "claimed", "executing", "executed", "succeeded", "failed", "replied",
                    "cancelled", "timed_out", "retried", "woken" }


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
    queued_at:    str | None = None
    started_at:   str | None = None
    completed_at: str | None = None
    current:      str | None = None             # the latest step this task's agent reported
    events:       list[ dict[ str, str ]] = []


def _iso( at: datetime | None ) -> str | None:
    return at.astimezone().isoformat( timespec="seconds" ) if at is not None else None


def event_rows( events: list[ TaskEvent ], scanner: SecretScanner ) -> list[ dict[ str, str ]]:
    """Display rows for one task's recorded events, most recent MAX_EVENTS, oldest first."""
    out = []
    for e in events[ -MAX_EVENTS: ]:
        if e.name == PROGRESS:
            phase   = str( e.details.get( "phase", "step" ))
            message = safe_error( str( e.details.get( "message", "" )), scanner ) or phase
        else:
            phase   = e.name.removeprefix( "task." )
            message = f"{ phase } ({ e.agent })"
        out.append({ "timestamp": _iso( e.at ), "agent": e.agent, "phase": phase, "message": message })
    return out


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


def row_for( envelope: Envelope, names: dict[ str, str ], now: datetime, scanner: SecretScanner,
             events: list[ TaskEvent ] | None = None ) -> WorkerTaskRow:
    status  = TaskStatus( envelope.status )
    queued  = envelope.enqueued_at or envelope.created_at
    started = envelope.dequeued_at
    ended   = envelope.completed_at
    workdir = str( envelope.context.get( "workdir" ) or "" )
    latest  = max( t for t in ( envelope.created_at, envelope.enqueued_at, started, ended ) if t is not None )
    shown   = event_rows( events or [], scanner )
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
        queued_at=_iso( queued ), started_at=_iso( started ), completed_at=_iso( ended ),
        current=next(( r[ "message" ] for r in reversed( shown ) if r[ "phase" ] not in LIFECYCLE ), None ),
        events=shown,
    )


def events_by_task( path: Path, wanted: set[ str ]) -> dict[ str, list[ TaskEvent ]]:
    grouped: dict[ str, list[ TaskEvent ]] = {}
    for event in JsonlEventLog( path ).all():
        if event.task_id in wanted:
            grouped.setdefault( event.task_id, []).append( event )
    return grouped


def _notes_entries( path: Path ) -> list[ dict ]:
    try:
        lines = path.read_text( encoding="utf-8", errors="replace" ).splitlines()[ -NOTES_TAIL: ]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads( line )
            entry[ "_at" ] = datetime.fromisoformat( str( entry[ "at" ]))
        except ( ValueError, KeyError, TypeError ):
            continue
        if isinstance( entry, dict ) and isinstance( entry.get( "event" ), str ):
            out.append( entry )
    return out


def _notes_message( entry: dict, scanner: SecretScanner ) -> str:
    parts = [ f"{ entry[ 'source' ]}: { entry[ 'event' ]}" if entry.get( "source" ) else entry[ "event" ]]
    if entry.get( "filename" ):
        parts.append( str( entry[ "filename" ]))
    if entry.get( "expected_revision" ) and entry.get( "revision" ):
        parts.append( f"{ str( entry[ 'expected_revision' ])[ :12 ]} -> { str( entry[ 'revision' ])[ :12 ]}" )
    elif entry.get( "revision" ):
        parts.append( f"@{ str( entry[ 'revision' ])[ :12 ]}" )
    if entry.get( "actor" ):
        parts.append( f"as { entry[ 'actor' ]}" )
    if entry.get( "detail" ):
        parts.append( f"— { entry[ 'detail' ]}" )
    return safe_error( " ".join( str( p ) for p in parts ), scanner ) or entry[ "event" ]


def notes_rows( path: Path, now: datetime, scanner: SecretScanner ) -> list[ WorkerTaskRow ]:
    """Worker Activity rows for the notes agent and the dashboard's Notes interface."""
    groups: dict[ str, list[ dict ]] = {}
    for number, entry in enumerate( _notes_entries( path )):
        key = str( entry.get( "task_id" ) or "" ) or f"notes-{ entry[ '_at' ].strftime( '%Y%m%dT%H%M%S' )}-{ number }"
        groups.setdefault( key, []).append( entry )
    rows = []
    for key, entries in groups.items():
        entries.sort( key=lambda e: e[ "_at" ])
        failures = [ e for e in entries if e[ "event" ] in NOTES_FAILED ]
        status   = "failed" if failures else "succeeded" if any( e[ "event" ] in NOTES_OK for e in entries ) else "unknown"
        first, last = entries[ 0 ][ "_at" ], entries[ -1 ][ "_at" ]
        shown = [{ "timestamp": _iso( e[ "_at" ]), "agent": "notes" if e.get( "source" ) == "worker" else "dashboard",
                   "phase": e[ "event" ], "message": _notes_message( e, scanner )} for e in entries[ -MAX_EVENTS: ]]
        op = next(( str( e[ "op" ]) for e in entries if e.get( "op" )), "" ) or entries[ 0 ][ "event" ]
        rows.append( WorkerTaskRow(
            id=key, agent="notes", operation=op, workspace="notes", status=status,
            queue_age=None, run_duration=duration( first, last ), attempts=1,
            last_update=_iso( last ), error=_notes_message( failures[ -1 ], scanner ) if failures else None,
            started_at=_iso( first ), completed_at=_iso( last ), current=shown[ -1 ][ "message" ], events=shown ))
    return rows


def build_rows( root: Path = MAILBOX_ROOT, workspaces_file: Path = WORKSPACES_FILE,
                now: datetime | None = None, limit: int = MAX_ROWS,
                event_log: Path | None = None, notes_log: Path | None = None ) -> list[ WorkerTaskRow ]:
    """Open work first, then the most recently touched. One row per bridge task."""
    now     = now or datetime.now( timezone.utc )
    names   = workspace_names( workspaces_file )
    scanner = SecretScanner()
    latest: dict[ str, Envelope ] = {}
    for envelope in bridge_requests( root ):
        key = str( envelope.context[ "bridge_task_id" ])
        if key not in latest or envelope.created_at > latest[ key ].created_at:
            latest[ key ] = envelope
    log    = event_log if event_log is not None else root.parent / "events.jsonl"
    events = events_by_task( log, { e.id for e in latest.values()})
    rows = [ row_for( e, names, now, scanner, events.get( e.id )) for e in latest.values()]
    if notes_log is not None:
        seen = { r.id for r in rows }
        rows += [ r for r in notes_rows( notes_log, now, scanner ) if r.id not in seen ]
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
    rows = build_rows( notes_log=NOTES_LOG )
    if args.print:
        print( json.dumps([ r.model_dump() for r in rows ], indent=1 ))
        return 0
    write_snapshot( rows, args.out )
    print( f"{ len( rows )} rows -> { args.out }" )
    return 0


if __name__ == "__main__":
    raise SystemExit( main())

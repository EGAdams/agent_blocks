"""AndroidPhotosAgent: the IAgentAdapter that lets the Star Trek Bridge look at the photographs on EG's Android
phone, plugged by USB into this Windows computer. Read-only, and no model in this path.

The prompt is JSON written by code (`submit_agent_task` to agent and workspace `android-photos`):

    {"android": "status"}
    {"android": "list_recent_photos", "count": 15}
    {"android": "get_photo", "number": 3}            or  {"android": "get_photo", "name": "IMG_20261114_093015.jpg"}
    {"android": "get_thumbnail", "number": 3}

    inspect    read_only    every one of them. There is no implement operation: nothing here changes the phone.

Each task is one run of `adws/adw_android_photos.py`. A request names an operation, a count, a number or a
file name -- never a path, a folder or a device -- and each value is checked here before it becomes a
`--flag=value`. The phone must be on this machine's own allowlist, which only EG edits, on this machine.

After a listing or a photo, the pictures are handed to the gateway through the same IDownloadPublisher a
browser download uses, so ChatGPT is shown the image itself. A listing publishes thumbnails only. What goes
back in the task record carries a `file_id` per photo and no local path.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .browser_agent import IDownloadPublisher
from .deploy_agent import DeployCommandFailed, IRunningDeployCommand
from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER  = "android_photos_adw"
OPERATIONS = frozenset({ "status", "list_recent_photos", "get_photo", "get_thumbnail" })
PUBLISHES  = frozenset({ "list_recent_photos", "get_photo", "get_thumbnail" })
MAX_COUNT  = 20
NAME       = r"[^\\/:*?\"<>|\x00-\x1f]{1,180}"
ALLOWS     = { "status": (), "list_recent_photos": ( "count", ), "get_photo": ( "number", "name" ),
               "get_thumbnail": ( "number", "name" )}
#: What the task record may say about one photograph. `original` (a local path) is not among them.
PHOTO_KEYS = ( "number", "name", "folder", "storage", "taken_at", "taken_source", "bytes" )
FILE_KEYS  = ( "number", "name", "mime", "bytes", "sha256", "file_id", "original_stored", "published", "note" )
DATA_KEYS  = ( "state", "device", "devices_seen", "folders", "photo_count", "total_on_phone", "notes", "next_step" )


class IAndroidPhotoCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ]) -> IRunningDeployCommand: ...


class AndroidPhotosAgent:
    def __init__( self, commands: IAndroidPhotoCommands, clock: IClock, publisher: IDownloadPublisher | None = None,
                  poll_seconds: float = 0.5, deadline_seconds: float = 420.0 ) -> None:
        self._commands, self._clock, self._publisher = commands, clock, publisher
        self._poll, self._deadline = poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "android" ), str ):
            return self._refused( 'an android task is JSON, e.g. {"android": "list_recent_photos", "count": 15}; operations: '
                                  + ", ".join( sorted( OPERATIONS )))
        what = request[ "android" ].removeprefix( "android_" )
        if what not in OPERATIONS:
            return self._refused( f"android photos does not offer { what[ :40 ]!r}; it only reads" )
        if task.operation != "inspect":
            return self._refused( f"{ what } runs as inspect, not { task.operation }" )
        unknown = set( request ) - set( ALLOWS[ what ]) - { "android" }
        if unknown:
            return self._refused( f"{ what } does not take: " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, top in (( "count", MAX_COUNT ), ( "number", MAX_COUNT )):
            value = request.get( key )
            if value is None:
                continue
            if isinstance( value, bool ) or not isinstance( value, int ) or not 1 <= value <= top:
                return self._refused( f"{ key } is a whole number from 1 to { top }" )
            flags[ key ] = str( value )
        name = request.get( "name" )
        if name is not None:
            if not isinstance( name, str ) or not re.fullmatch( NAME, name.strip()) or name.strip().startswith(( "-", "." )):
                return self._refused( "name is a photo's file name, with no folder" )
            flags[ "name" ] = name.strip()
        if what in ( "get_photo", "get_thumbnail" ) and ( "number" in flags ) == ( "name" in flags ):
            return self._refused( f"{ what } needs a number from the last listing, or a name -- one of them" )
        flags[ "task-id" ] = task.task_id
        flags[ "client" ] = task.sender
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags ), keep_alive )
        except DeployCommandFailed as exc:
            return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, retryable=True,
                                 summary=f"the android photos ADW gave no outcome ({ exc })"[ :300 ],
                                 details={ "android": what, "ok": False, "verified": False })
        if signal == "lost":
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( task, answer, what )

    def _wait( self, running: IRunningDeployCommand, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. Every operation is a read, so any of them may be stopped."""
        deadline = self._clock.monotonic() + self._deadline
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, "continue"
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise DeployCommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                running.stop()
                return {}, signal
            self._clock.sleep( self._poll )

    def _outcome( self, task: ClaimedTask, answer: dict, what: str ) -> AgentOutcome:
        data     = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line     = str( answer.get( "line" ) or "" )[ :1000 ]
        ok       = bool( answer.get( "ok" ))
        files    = [ f for f in data.get( "files" ) or [] if isinstance( f, dict )]
        problem  = ""
        if ok and what in PUBLISHES and files:
            files, problem = self._published( task, files )
        ids      = { f.get( "number" ): f.get( "file_id" ) for f in files if f.get( "file_id" )}
        photos   = [{ **{ k: p.get( k ) for k in PHOTO_KEYS if k in p }, "file_id": ids.get( p.get( "number" ))}
                    for p in data.get( "photos" ) or [] if isinstance( p, dict )]
        shown    = ok and not problem and ( what == "status" or not photos or any( p[ "file_id" ] for p in photos ))
        verified = shown and answer.get( "verified" ) is True
        details  = { "android": what, "ok": ok and not problem, "verified": verified, "retryable": bool( answer.get( "retryable" )),
                     "adw_id": answer.get( "adw_id" ), **{ k: data[ k ] for k in DATA_KEYS if data.get( k ) not in ( None, "", [])},
                     "photos": photos, "files": [{ k: f[ k ] for k in FILE_KEYS if k in f } for f in files ],
                     "inspectable": bool( ids )}
        if problem:
            details[ "inspect_problem" ] = problem
            line = f"{ line } But the pictures could not be sent to the gateway: { problem }"[ :1000 ]
        body = line + "\n\n" + json.dumps({ k: details[ k ] for k in details if k != "files" }, indent=1, default=str )[ :12000 ]
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the phone could not be read" )[ :300 ],
                             body=body, retryable=bool( answer.get( "retryable" )) or bool( problem ), details=details )

    def _published( self, task: ClaimedTask, files: list[ dict ]) -> tuple[ list[ dict ], str ]:
        """( the files, each with its `file_id` ), or ( the files, why they could not be published )."""
        if self._publisher is None:
            return files, "this worker has no gateway publisher wired"
        out = self._publisher.publish( task, { "files": files })
        published = [ f for f in out.get( "files" ) or [] if isinstance( f, dict )]
        if not out.get( "inspectable" ):
            return published, str( out.get( "inspect_problem" ) or "the gateway did not take the pictures" )[ :300 ]
        return published, ""

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )

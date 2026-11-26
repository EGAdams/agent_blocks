"""SheilaOperations -- the deterministic half of Sheila: status, Bridge view, Post Office messages and ADW runs.

Sheila's free-text work goes to Pi (`PiCodingAgentExecutor`, jailed). A request whose text is a JSON object with a
`sheila` key is one of her *operations* instead, run by code with no model in the path, outside the jail, so that
what she reports about herself, the Bridge, her messages and the ADWs she ran is read from the system rather than
described by a model:

    {"sheila": "status"}                                                 inspect: the source-of-truth snapshot
    {"sheila": "bridge"}                                                 inspect: gateway health and agent discovery
    {"sheila": "message", "to": "hello-world", "task_type": "hello",
     "text": "ping"}                                                     inspect: a Post Office round trip
    {"sheila": "run_adw", "adw": "machine_watch_dry_run"}                inspect or implement, per the ADW's entry

Patterns: `SheilaOperations` is a Decorator over her Pi executor (it answers operations, passes everything else
through unchanged) and holds a Registry of `ISheilaOperation` Strategies, one per operation. Every operation's
outcome, and every Pi task's, is appended to one JSONL ledger, which `status` reads back for "last successful
task" and "last successful ADW". Nothing here can widen what Sheila may do: the recipients she may message and the
ADWs she may run are fixed lists on her card, and a write ADW runs only under `implement`.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
import re
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..models import Envelope, TaskRequest, TaskResult
from ..ports import IRequestReply, ITaskDispatcher, ITaskExecutor

PERFORMER  = "sheila:operations"
READ_TYPES = frozenset({ "inspect", "explain", "ask" })
WRITE_TYPE = "implement"
ADW_ID     = re.compile( r"adw_id:\s*([0-9a-f]{8})" )
TAIL       = 1500


def utc_now() -> str:
    return datetime.now( timezone.utc ).strftime( "%Y-%m-%dT%H:%M:%SZ" )


# -- data crossing the boundary ----------------------------------------------------------------------------------

class LedgerEntry( BaseModel ):
    """One line of Sheila's ledger: what ran, how it ended, and the ids that trace it."""
    model_config = ConfigDict( extra="forbid" )

    at:             str
    kind:           str                         # an operation name, or "pi"
    task_type:      str
    ok:             bool
    summary:        str = ""
    local_task_id:  str | None = None
    bridge_task_id: str | None = None
    adw_id:         str | None = None
    adw:            str | None = None


class AdwEntry( BaseModel ):
    """One ADW Sheila may run: fixed argv (relative paths are under the workspace), and whether it writes."""
    model_config = ConfigDict( extra="forbid" )

    argv:            list[ str ] = Field( min_length=1 )
    writes:          bool = False
    timeout_seconds: float = Field( default=300.0, gt=0, le=3600 )
    about:           str = ""


class CommandOutcome( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    code:      int | None
    stdout:    str = ""
    stderr:    str = ""
    timed_out: bool = False


# -- interfaces ----------------------------------------------------------------------------------------------------

class ISheilaLedger( Protocol ):
    def append( self, entry: LedgerEntry ) -> None: ...
    def entries( self ) -> list[ LedgerEntry ]: ...


class ICommandRunner( Protocol ):
    def run( self, argv: list[ str ], cwd: Path, timeout_seconds: float, env: dict[ str, str ] | None = None ) -> CommandOutcome: ...


class IBridgeView( Protocol ):
    def health( self ) -> dict[ str, Any ]: ...
    def agents( self ) -> list[ dict[ str, Any ]] | None: ...     # None: Sheila has no read token here


class ISheilaProbe( Protocol ):
    """One fact for `status`: a name and a JSON-able value. A probe never raises; a problem is its value."""
    @property
    def name( self ) -> str: ...
    def look( self ) -> dict[ str, Any ]: ...


class ISheilaOperation( Protocol ):
    @property
    def name( self ) -> str: ...
    def writes( self, request: dict[ str, Any ]) -> bool: ...
    def run( self, request: dict[ str, Any ], envelope: Envelope ) -> TaskResult: ...


# -- concretes small enough to live beside their interfaces ---------------------------------------------------------

class JsonlSheilaLedger:
    def __init__( self, path: Path, keep: int = 2000 ) -> None:
        self._path, self._keep = path, keep

    def append( self, entry: LedgerEntry ) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with self._path.open( "a", encoding="utf-8" ) as handle:
            handle.write( entry.model_dump_json() + "\n" )

    def entries( self ) -> list[ LedgerEntry ]:
        if not self._path.exists():
            return []
        out: list[ LedgerEntry ] = []
        for line in self._path.read_text( encoding="utf-8", errors="replace" ).splitlines()[ -self._keep: ]:
            try:
                out.append( LedgerEntry.model_validate_json( line ))
            except ValueError:
                continue                            # a torn line is skipped, never fatal
        return out


class SubprocessRunner:
    def run( self, argv: list[ str ], cwd: Path, timeout_seconds: float, env: dict[ str, str ] | None = None ) -> CommandOutcome:
        try:
            done = subprocess.run( argv, cwd=str( cwd ), capture_output=True, text=True, errors="replace",
                                   timeout=timeout_seconds, stdin=subprocess.DEVNULL, env=env )
        except subprocess.TimeoutExpired as exc:
            return CommandOutcome( code=None, stdout=str( exc.stdout or "" )[ -TAIL: ], stderr=str( exc.stderr or "" )[ -TAIL: ],
                                   timed_out=True )
        except OSError as exc:
            return CommandOutcome( code=None, stderr=f"{ type( exc ).__name__ }: { exc }" )
        return CommandOutcome( code=done.returncode, stdout=done.stdout, stderr=done.stderr )


class HttpBridgeView:
    """IBridgeView over the gateway: `/health` needs no token; `/agents` needs a client token. Sheila's is bound in
    the gateway's `client_scopes` to no operation, agent or workspace, so it can read discovery and submit nothing."""

    def __init__( self, base_url: str, token_file: Path | None, timeout: float = 15.0 ) -> None:
        self._base, self._token_file, self._timeout = base_url.rstrip( "/" ), token_file, timeout

    def _get( self, path: str, token: str | None ) -> tuple[ int, Any ]:
        request = urllib.request.Request( self._base + path, method="GET" )
        if token:
            request.add_header( "Authorization", f"Bearer { token }" )
        try:
            with urllib.request.urlopen( request, timeout=self._timeout ) as response:
                return response.status, json.loads( response.read() or b"{}" )
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except ( urllib.error.URLError, OSError, ValueError ) as exc:
            return 0, f"{ type( exc ).__name__ }: { exc }"[ :200 ]

    def health( self ) -> dict[ str, Any ]:
        started = time.monotonic()
        status, body = self._get( "/health", None )
        ok = status == 200 and isinstance( body, dict ) and bool( body.get( "ok" ))
        out: dict[ str, Any ] = { "ok": ok, "status": status, "ms": int(( time.monotonic() - started ) * 1000 )}
        if status == 0:
            out[ "error" ], out[ "network" ] = body, "unreachable"
        return out

    def agents( self ) -> list[ dict[ str, Any ]] | None:
        if self._token_file is None or not self._token_file.is_file():
            return None
        token = self._token_file.read_text( encoding="utf-8" ).strip()
        status, body = self._get( "/agents", token )
        if status != 200 or not isinstance( body, dict ):
            raise RuntimeError( f"agent discovery answered HTTP { status }" )
        return [ a for a in body.get( "agents", []) if isinstance( a, dict )]


# -- probes --------------------------------------------------------------------------------------------------------

class HostProbe:
    name = "host"

    def look( self ) -> dict[ str, Any ]:
        return { "hostname": socket.gethostname(), "pid": os.getpid()}


class PiRuntimeProbe:
    """Pi's binary and version, run the way her executor runs it (node from the same install)."""
    name = "pi"

    def __init__( self, binary: str, model: str, runner: ICommandRunner ) -> None:
        self._binary, self._model, self._runner = binary, model, runner

    def look( self ) -> dict[ str, Any ]:
        path = Path( self._binary )
        if not path.exists():
            return { "installed": False, "binary": self._binary, "model": self._model }
        env  = { **os.environ, "PATH": f"{ path.parent }:{ os.environ.get( 'PATH', '' ) }" }
        done = self._runner.run([ self._binary, "--version" ], Path.home(), 20.0, env )
        version = ( done.stdout or done.stderr ).strip().splitlines()[ -1 ][ :60 ] if ( done.stdout or done.stderr ).strip() else ""
        return { "installed": True, "binary": self._binary, "model": self._model,
                 "version": version if done.code == 0 else None,
                 "version_error": None if done.code == 0 else ( done.stderr or "no output" )[ -200: ]}


class LoginProbe:
    """How long her provider login has left -- seconds only, never a value from the file."""
    name = "login"

    def __init__( self, seconds_left: Callable[[], float ]) -> None:
        self._seconds_left = seconds_left

    def look( self ) -> dict[ str, Any ]:
        try:
            left = float( self._seconds_left())
        except Exception as exc:                    # noqa: BLE001 -- reported, never hidden
            return { "ok": False, "error": f"{ type( exc ).__name__ }: { exc }"[ :200 ]}
        return { "ok": left > 600, "seconds_left": int( left ), "days_left": round( left / 86400, 1 )}


class JailProbe:
    name = "jail"

    def __init__( self, bwrap: str = "/usr/bin/bwrap" ) -> None:
        self._bwrap = bwrap

    def look( self ) -> dict[ str, Any ]:
        return { "bwrap": self._bwrap, "present": Path( self._bwrap ).exists()}


class UnitProbe:
    """A systemd user unit's state, e.g. the machine-lane Bridge worker that carries her tasks."""

    def __init__( self, name: str, unit: str, runner: ICommandRunner ) -> None:
        self.name, self._unit, self._runner = name, unit, runner

    def look( self ) -> dict[ str, Any ]:
        done = self._runner.run([ "systemctl", "--user", "is-active", self._unit ], Path.home(), 10.0 )
        state = ( done.stdout or done.stderr ).strip()[ :40 ] or "unknown"
        return { "unit": self._unit, "state": state, "active": state == "active" }


class WorkspaceProbe:
    def __init__( self, name: str, path: Path ) -> None:
        self.name, self._path = name, path

    def look( self ) -> dict[ str, Any ]:
        if not self._path.is_dir():
            return { "path": str( self._path ), "present": False }
        names = sorted( p.name for p in self._path.iterdir())[ :40 ]
        return { "path": str( self._path ), "present": True, "files": names }


class AcceptanceProbe:
    """The latest acceptance results, as recorded in a JSON file, with its age -- labelled historical if old."""
    name = "acceptance"

    def __init__( self, path: Path, fresh_seconds: float = 7 * 86400 ) -> None:
        self._path, self._fresh = path, fresh_seconds

    def look( self ) -> dict[ str, Any ]:
        if not self._path.is_file():
            return { "recorded": False, "file": str( self._path )}
        try:
            data = json.loads( self._path.read_text( encoding="utf-8" ))
        except ValueError as exc:
            return { "recorded": False, "file": str( self._path ), "error": str( exc )[ :200 ]}
        age = time.time() - self._path.stat().st_mtime
        tests = data.get( "tests", []) if isinstance( data, dict ) else []
        passed = sum( 1 for t in tests if isinstance( t, dict ) and t.get( "result" ) == "PASS" )
        return { "recorded": True, "file": str( self._path ), "run_at": data.get( "run_at" ), "passed": passed,
                 "total": len( tests ), "label": "current" if age <= self._fresh else "historical" }


# -- operations ----------------------------------------------------------------------------------------------------

def _fields( request: dict[ str, Any ], allowed: set[ str ]) -> str | None:
    unknown = set( request ) - allowed - { "sheila" }
    return "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )) if unknown else None


class StatusOperation:
    """Sheila's source of truth: every probe, plus the ledger's last successes and last error, timestamped."""
    name = "status"

    def __init__( self, probes: list[ ISheilaProbe ], ledger: ISheilaLedger, machine: str, card_version: str ) -> None:
        self._probes, self._ledger, self._machine, self._version = probes, ledger, machine, card_version

    def writes( self, request: dict[ str, Any ]) -> bool:
        return False

    def snapshot( self ) -> dict[ str, Any ]:
        facts = {}
        for probe in self._probes:
            try:
                facts[ probe.name ] = probe.look()
            except Exception as exc:                # noqa: BLE001 -- one bad probe never hides the others
                facts[ probe.name ] = { "error": f"{ type( exc ).__name__ }: { exc }"[ :200 ]}
        entries = self._ledger.entries()
        def last( pred ) -> dict[ str, Any ] | None:
            hit = next(( e for e in reversed( entries ) if pred( e )), None )
            return hit.model_dump() if hit else None
        blockers = [ f"{ name }: { why }" for name, why in (
            ( "pi", "not installed" if not facts.get( "pi", {}).get( "installed" ) else
                    None if facts[ "pi" ].get( "version" ) else "does not start" ),
            ( "login", None if facts.get( "login", {}).get( "ok" ) else "stale or unreadable; run pi and /login on BATI" ),
            ( "jail", None if facts.get( "jail", {}).get( "present" ) else "bubblewrap missing" ),
            ( "worker", None if facts.get( "worker", {}).get( "active", True ) else "the machine-lane Bridge worker is not active" ),
        ) if why ]
        return {
            "agent": "sheila", "machine": self._machine, "observed_at": utc_now(), "version": self._version,
            "runtime_state": "ready" if not blockers else "degraded",
            "blockers": blockers, "facts": facts,
            "last_success":     last( lambda e: e.ok and e.kind != "status" ),
            "last_adw_success": last( lambda e: e.ok and e.kind == "run_adw" ),
            "last_error":       last( lambda e: not e.ok ),
            "last_message":     last( lambda e: e.kind == "message" ),
            "tasks_recorded":   len( entries ),
        }

    def run( self, request: dict[ str, Any ], envelope: Envelope ) -> TaskResult:
        problem = _fields( request, set())
        if problem:
            return TaskResult( ok=False, text=problem, performer=PERFORMER )
        snap = self.snapshot()
        snap[ "current_task" ] = { "local_task_id": envelope.id, "bridge_task_id": envelope.context.get( "bridge_task_id" ),
                                   "sender": envelope.sender }
        return TaskResult( ok=True, text=json.dumps( snap, indent=1 ), performer=PERFORMER,
                           details={ "operation": "status", "runtime_state": snap[ "runtime_state" ]})


class BridgeOperation:
    """What Sheila can see of the Star Trek Bridge: gateway health, agent discovery, and the task she is in."""
    name = "bridge"

    def __init__( self, view: IBridgeView ) -> None:
        self._view = view

    def writes( self, request: dict[ str, Any ]) -> bool:
        return False

    def run( self, request: dict[ str, Any ], envelope: Envelope ) -> TaskResult:
        problem = _fields( request, set())
        if problem:
            return TaskResult( ok=False, text=problem, performer=PERFORMER )
        health = self._view.health()
        agents = self._view.agents()
        body = {
            "observed_at": utc_now(), "gateway": health,
            "agents": None if agents is None else [{ "name": a.get( "name" ), "operations": a.get( "operations" ),
                                                     "workspaces": a.get( "workspaces" )} for a in agents ],
            "discovery": "unavailable: no read-only Bridge token on this machine" if agents is None else "ok",
            "this_task": { "bridge_task_id": envelope.context.get( "bridge_task_id" ), "local_task_id": envelope.id,
                           "sender": envelope.sender, "task_type": envelope.task_type },
        }
        ok = bool( health.get( "ok" ))
        return TaskResult( ok=ok, text=json.dumps( body, indent=1 ), performer=PERFORMER,
                           details={ "operation": "bridge", "agents": None if agents is None else len( agents )})


class MessageOperation:
    """A Post Office round trip from Sheila: send to an allowed recipient, wait for the correlated reply."""
    name = "message"

    def __init__( self, dispatcher: ITaskDispatcher, replies: IRequestReply, recipients: dict[ str, list[ str ]],
                  wait_seconds: float = 120.0, sender: str = "sheila" ) -> None:
        self._dispatcher, self._replies, self._recipients = dispatcher, replies, recipients
        self._wait, self._sender = wait_seconds, sender

    def writes( self, request: dict[ str, Any ]) -> bool:
        return False

    def run( self, request: dict[ str, Any ], envelope: Envelope ) -> TaskResult:
        problem = _fields( request, { "to", "task_type", "text" })
        if problem:
            return TaskResult( ok=False, text=problem, performer=PERFORMER )
        to, kind = str( request.get( "to", "" )), str( request.get( "task_type", "" ))
        text = str( request.get( "text", "" ))[ :2000 ] or "ping from Sheila"
        if kind not in self._recipients.get( to, []):
            allowed = "; ".join( f"{ r }: { ', '.join( t )}" for r, t in sorted( self._recipients.items()))
            return TaskResult( ok=False, performer=PERFORMER,
                               text=f"Sheila may not send { kind or '(no task_type)' } to { to or '(no recipient)' }; allowed: { allowed }" )
        receipt = self._dispatcher.send( TaskRequest( sender=self._sender, recipient=to, task_type=kind, text=text,
                                                      context={ "bridge_task_id": envelope.context.get( "bridge_task_id" )},
                                                      wait_seconds=0, parent_id=envelope.id ))
        if not receipt.accepted:
            return TaskResult( ok=False, performer=PERFORMER, text=f"the post office refused it: { receipt.reason }",
                               details={ "operation": "message", "to": to })
        reply = self._replies.await_reply( self._sender, receipt.task_id, self._wait )
        body = { "observed_at": utc_now(), "sent_task_id": receipt.task_id, "to": to, "task_type": kind,
                 "reply_id": reply.id if reply else None, "correlation_id": reply.correlation_id if reply else None,
                 "outcome": reply.outcome.value if reply and reply.outcome else None,
                 "reply_text": ( reply.result or reply.error or "" )[ :2000 ] if reply else None,
                 "performer": reply.performer if reply else None }
        if reply is None:
            return TaskResult( ok=False, text=json.dumps( body, indent=1 ) + f"\nno reply within { int( self._wait )}s",
                               performer=PERFORMER, details={ "operation": "message", "sent_task_id": receipt.task_id })
        ok = reply.correlation_id == receipt.task_id and ( reply.outcome is None or reply.outcome.value == "succeeded" )
        return TaskResult( ok=ok, text=json.dumps( body, indent=1 ), performer=PERFORMER,
                           details={ "operation": "message", "sent_task_id": receipt.task_id, "reply_id": reply.id })


class RunAdwOperation:
    """Run one ADW from the card's fixed list and return its ADW id, exit code and output tail."""
    name = "run_adw"

    def __init__( self, adws: dict[ str, AdwEntry ], workspace: Path, runner: ICommandRunner ) -> None:
        self._adws, self._workspace, self._runner = adws, workspace, runner

    def writes( self, request: dict[ str, Any ]) -> bool:
        entry = self._adws.get( str( request.get( "adw", "" )))
        return bool( entry and entry.writes )

    def run( self, request: dict[ str, Any ], envelope: Envelope ) -> TaskResult:
        problem = _fields( request, { "adw" })
        if problem:
            return TaskResult( ok=False, text=problem, performer=PERFORMER )
        name  = str( request.get( "adw", "" ))
        entry = self._adws.get( name )
        if entry is None:
            return TaskResult( ok=False, performer=PERFORMER,
                               text=f"no ADW called { name[ :40 ]!r} on Sheila's list; she may run: { ', '.join( sorted( self._adws ))}" )
        argv = [ a if Path( a ).is_absolute() or "/" not in a else str( self._workspace / a ) for a in entry.argv ]
        started = time.monotonic()
        done = self._runner.run( argv, self._workspace, entry.timeout_seconds )
        found = ADW_ID.search( done.stdout or "" ) or ADW_ID.search( done.stderr or "" )
        body = { "observed_at": utc_now(), "adw": name, "adw_id": found.group( 1 ) if found else None,
                 "exit_code": done.code, "timed_out": done.timed_out, "seconds": round( time.monotonic() - started, 1 ),
                 "stdout_tail": ( done.stdout or "" )[ -TAIL: ], "stderr_tail": ( done.stderr or "" )[ -600: ]}
        ok = done.code == 0 and not done.timed_out
        return TaskResult( ok=ok, text=json.dumps( body, indent=1 ), performer=PERFORMER,
                           details={ "operation": "run_adw", "adw": name, "adw_id": body[ "adw_id" ], "exit_code": done.code })


# -- the Decorator -------------------------------------------------------------------------------------------------

class SheilaOperations:
    """ITaskExecutor: a `{"sheila": ...}` request is an operation; anything else goes to Pi. Both are ledgered."""

    def __init__( self, inner: ITaskExecutor, operations: list[ ISheilaOperation ], ledger: ISheilaLedger ) -> None:
        self._inner, self._ledger = inner, ledger
        self._operations = { op.name: op for op in operations }

    @property
    def performer( self ) -> str:
        return f"{ PERFORMER }+{ self._inner.performer }"

    @staticmethod
    def request_of( text: str ) -> dict[ str, Any ] | None:
        stripped = text.strip()
        if not stripped.startswith( "{" ):
            return None
        try:
            value = json.loads( stripped )
        except ValueError:
            return None
        return value if isinstance( value, dict ) and isinstance( value.get( "sheila" ), str ) else None

    async def execute( self, envelope: Envelope ) -> TaskResult:
        request = self.request_of( envelope.text )
        if request is None:
            result = await self._inner.execute( envelope )
            self._record( "pi", envelope, result )
            return result
        # an operation blocks (a subprocess, a reply wait), so it runs off the event loop
        result = await asyncio.to_thread( self._operate, request, envelope )
        self._record( str( request[ "sheila" ])[ :40 ], envelope, result )
        return result

    def _operate( self, request: dict[ str, Any ], envelope: Envelope ) -> TaskResult:
        op = self._operations.get( request[ "sheila" ])
        if op is None:
            return TaskResult( ok=False, performer=PERFORMER,
                               text=f"Sheila has no operation { request[ 'sheila' ][ :40 ]!r}; she has: { ', '.join( sorted( self._operations ))}" )
        writes = op.writes( request )
        if writes and envelope.task_type != WRITE_TYPE:
            return TaskResult( ok=False, performer=PERFORMER, text=f"{ op.name } here writes, so it runs as implement, not { envelope.task_type }" )
        if not writes and envelope.task_type not in READ_TYPES:
            return TaskResult( ok=False, performer=PERFORMER, text=f"{ op.name } is read-only, so it runs as inspect, not { envelope.task_type }" )
        try:
            return op.run( request, envelope )
        except Exception as exc:                    # noqa: BLE001 -- a failed operation is a result, never a crash
            return TaskResult( ok=False, performer=PERFORMER, text=f"{ op.name } failed: { type( exc ).__name__ }: { exc }"[ :500 ])

    def _record( self, kind: str, envelope: Envelope, result: TaskResult ) -> None:
        details = result.details or {}
        try:
            self._ledger.append( LedgerEntry(
                at=utc_now(), kind=kind, task_type=envelope.task_type, ok=result.ok,
                summary=" ".join(( result.text or "" ).split())[ :200 ],
                local_task_id=envelope.id, bridge_task_id=envelope.context.get( "bridge_task_id" ),
                adw_id=details.get( "adw_id" ), adw=details.get( "adw" )))
        except OSError:
            pass                                    # the ledger is best effort; the result is what matters


def new_adw_id() -> str:
    return uuid.uuid4().hex[ :8 ]

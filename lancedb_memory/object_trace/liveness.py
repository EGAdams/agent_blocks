"""How an object looks *now*. A stored record is never, by itself, proof that anything is alive.

An unfinished object is `stale` once it has been silent for longer than it
promised to be (three heartbeat periods, or DEFAULT_WINDOW when it promised
nothing), or once its process is known to be gone. Otherwise its display is
what its last event said: heartbeat, progress, or waiting. A finished object
is what it finished as.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Callable

from .models import Display, ObjectView, TraceEvent, TraceObject

DEFAULT_WINDOW = 300.0
HOST = socket.gethostname()


def local_pid_alive( host: str, pid: int | None ) -> bool | None:
    """False only when this machine can see the process is gone; None when it cannot tell."""
    if not pid or host != HOST:
        return None
    try:
        os.kill( pid, 0 )
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def view( obj: TraceObject, now: float, events: list[ TraceEvent ] | None = None, dependents: list[ str ] | None = None,
          pid_alive: Callable[[ str, int | None ], bool | None ] = local_pid_alive ) -> ObjectView:
    last = obj.last_event_at if obj.last_event_at is not None else obj.created_at
    age  = max( 0.0, now - last )
    display, alive, reason = _derive( obj, age, pid_alive )
    return ObjectView( object=obj, display=display, alive=alive, age_seconds=round( age, 1 ), reason=reason,
                       events=events or [], dependents=dependents or [])


def _derive( obj: TraceObject, age: float, pid_alive: Callable[[ str, int | None ], bool | None ]) -> tuple[ Display, bool | None, str ]:
    if obj.state in ( "failed", "construct_failed" ):
        what = "construction failed" if obj.state == "construct_failed" else "failed"
        return "failed", False, f"{ what } { _ago( age )}" + ( f": { obj.error.get( 'message', '' )[ :160 ]}" if obj.error else "" )
    if obj.state == "succeeded":
        return "succeeded", False, f"finished { _ago( age )}"
    if obj.state == "cancelled":
        return "cancelled", False, f"cancelled { _ago( age )}"
    window = obj.heartbeat_seconds * 3 if obj.heartbeat_seconds else DEFAULT_WINDOW
    if age > window:
        promise = f"it promised an event every { obj.heartbeat_seconds:g}s" if obj.heartbeat_seconds else f"no heartbeat was promised; the default window is { DEFAULT_WINDOW:g}s"
        return "stale", False, f"no event for { age:.0f}s while { obj.state }; { promise }"
    if pid_alive( obj.host, obj.pid ) is False:
        return "stale", False, f"process { obj.pid } on { obj.host } is gone, but the object never finished"
    kind = obj.last_event_kind or "created"
    if kind == "heartbeat":
        return "heartbeat", True, f"heartbeat { _ago( age )}"
    if kind == "waiting" or obj.state == "waiting":
        return "waiting", True, f"waiting, last event { _ago( age )}"
    if kind == "created" and obj.state == "created":
        return "created", None, f"made { _ago( age )}, nothing since"
    return "progress", True, f"last event ({ kind }) { _ago( age )}"


def _ago( age: float ) -> str:
    if age < 90:
        return f"{ age:.0f}s ago"
    if age < 5400:
        return f"{ age / 60:.0f}m ago"
    return f"{ age / 3600:.1f}h ago"

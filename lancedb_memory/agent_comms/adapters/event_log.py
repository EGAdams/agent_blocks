"""Observer plumbing: one publisher fans out to many subscribers, and a JSONL
log that is both a subscriber and the read side the board and mechanic use.

Swarm-Forge replaced its logbook with handoff headers; here the headers are
the envelope's own timestamps, and this log adds what a header cannot hold --
every attempt, every retry, who executed it and why it failed.
"""
from __future__ import annotations

import fcntl
from pathlib import Path

from ..models import TaskEvent
from ..ports import IEventSubscriber


class FanOutPublisher:
    """IEventPublisher that tells every subscriber, in order.

    A subscriber that raises is skipped, not fatal: an observer must never be
    able to lose the task it is observing (agent_memory's observers follow the
    same rule).
    """

    def __init__( self, subscribers: list[ IEventSubscriber ] | None = None ) -> None:
        self._subscribers = list( subscribers or [])
        self.errors: list[ str ] = []

    def subscribe( self, subscriber: IEventSubscriber ) -> None:
        self._subscribers.append( subscriber )

    def publish( self, event: TaskEvent ) -> None:
        for subscriber in self._subscribers:
            try:
                subscriber.notify( event )
            except Exception as exc:   # noqa: BLE001 -- see the class docstring
                self.errors.append( f"{ type( subscriber ).__name__ }: { exc }" )


class JsonlEventLog:
    """IEventSubscriber + IEventLog over one append-only JSONL file."""

    def __init__( self, path: Path ) -> None:
        self._path = Path( path )

    def notify( self, event: TaskEvent ) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with open( self._path, "a", encoding="utf-8" ) as handle:
            fcntl.flock( handle, fcntl.LOCK_EX )   # several workers append at once
            handle.write( event.model_dump_json() + "\n" )

    def events_for( self, task_id: str ) -> list[ TaskEvent ]:
        return [ e for e in self.all() if e.task_id == task_id ]

    def all( self ) -> list[ TaskEvent ]:
        if not self._path.exists():
            return []
        events = []
        for line in self._path.read_text( encoding="utf-8" ).splitlines():
            try:
                events.append( TaskEvent.model_validate_json( line ))
            except ValueError:
                continue   # a torn last line from a killed writer is not fatal
        return events


class RecordingSubscriber:
    """Keeps every event in memory. For tests and in-process callers."""

    def __init__( self ) -> None:
        self.events: list[ TaskEvent ] = []

    def notify( self, event: TaskEvent ) -> None:
        self.events.append( event )

    def names( self ) -> list[ str ]:
        return [ e.name for e in self.events ]

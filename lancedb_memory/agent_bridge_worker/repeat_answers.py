"""RepeatAnswers: answer a repeated read-only request from its last successful run.

Why (2026-11-04): ChatGPT re-submits a request it sees sitting "queued" -- one
prompt arrived 36 times in three hours. The worker runs one task at a time,
about a minute each, so every copy pushed real work further back and new tasks
sat queued with zero attempts.

Decorator over any `IAgentAdapter`: for a `read_only` task whose agent,
workspace, operation and prompt match one that SUCCEEDED within
`window_seconds`, it returns that answer at once and says which task it came
from and how old it is. Everything else -- writes, device control, failures,
older answers -- runs exactly as before. The store keeps a hash of the request,
never the prompt.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IAgentAdapter, Signal

DEFAULT_WINDOW_SECONDS = 900.0
MAX_ENTRIES            = 200


class IAnswerStore( Protocol ):
    def get( self, key: str ) -> dict | None: ...
    def put( self, key: str, entry: dict ) -> None: ...


def request_key( task: ClaimedTask ) -> str:
    raw = "\x1f".join(( task.agent, task.workspace, task.operation, task.prompt ))
    return hashlib.sha256( raw.encode( "utf-8" )).hexdigest()


class JsonAnswerStore:
    """Recent answers in one small 0600 JSON file, newest MAX_ENTRIES kept."""

    def __init__( self, path: Path ) -> None:
        self._path = path

    def _load( self ) -> dict:
        try:
            data = json.loads( self._path.read_text( encoding="utf-8" ))
            return data if isinstance( data, dict ) else {}
        except ( OSError, ValueError ):
            return {}

    def get( self, key: str ) -> dict | None:
        entry = self._load().get( key )
        return entry if isinstance( entry, dict ) else None

    def put( self, key: str, entry: dict ) -> None:
        data = self._load()
        data[ key ] = entry
        newest = sorted( data.items(), key=lambda kv: kv[ 1 ].get( "saved_at", 0 ), reverse=True )[ :MAX_ENTRIES ]
        self._path.parent.mkdir( parents=True, exist_ok=True )
        fd, tmp = tempfile.mkstemp( dir=self._path.parent, suffix=".tmp" )
        with os.fdopen( fd, "w", encoding="utf-8" ) as handle:
            json.dump( dict( newest ), handle )
        os.chmod( tmp, 0o600 )
        os.replace( tmp, self._path )


def _age( seconds: float ) -> str:
    seconds = max( 0, int( seconds ))
    return f"{ seconds }s" if seconds < 60 else f"{ seconds // 60 }m { seconds % 60:02d}s"


class RepeatAnswers:
    """Decorator: same read-only request answered recently -> that answer, labelled as reused."""

    def __init__( self, inner: IAgentAdapter, store: IAnswerStore, now: Callable[[], float ],
                  window_seconds: float = DEFAULT_WINDOW_SECONDS ) -> None:
        self._inner  = inner
        self._store  = store
        self._now    = now
        self._window = window_seconds

    def lines( self, local_task_id: str ) -> list[ str ]:
        return self._inner.lines( local_task_id )        # type: ignore[attr-defined]

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        if task.approval_mode != "read_only":
            return self._inner.run( task, workdir, keep_alive )
        key   = request_key( task )
        entry = self._store.get( key )
        now   = self._now()
        if entry and 0 <= now - float( entry.get( "saved_at", 0 )) <= self._window:
            note = ( f"Same request as { entry.get( 'task_id' ) }, answered { _age( now - float( entry[ 'saved_at' ])) } "
                     "ago; reused that answer instead of running it again." )
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, summary=f"{ note } { entry.get( 'summary', '' )}".strip(),
                                 body=entry.get( "body", "" ), performer=entry.get( "performer", "" ))
        outcome = self._inner.run( task, workdir, keep_alive )
        if outcome.kind is OutcomeKind.SUCCEEDED:
            try:
                self._store.put( key, { "task_id": task.task_id, "saved_at": now, "summary": outcome.summary,
                                        "body": outcome.body, "performer": outcome.performer })
            except OSError:
                pass                                     # a cache that can't be written must never fail the task
        return outcome

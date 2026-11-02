"""ITodoStore implementations: an append-only JSONL file, and memory for tests.

Every change appends the item's whole new version, so the file is also the
history: when something was added, by whom, and when it was checked off.
The current list is the last line per number. Nothing is ever rewritten or
removed, which is what lets a wrong "mark it done" be seen and undone by hand.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .models import Todo, TodoState


def default_home() -> Path:
    """Outside every repository: `COMPANY_TODOS_HOME`, else `~/company_todos`."""
    return Path( os.environ.get( "COMPANY_TODOS_HOME", Path.home() / "company_todos" ))


class _Ledger:
    """The add/complete rules, shared by both stores. Storage is the subclass's."""

    def _versions( self ) -> list[ Todo ]:
        raise NotImplementedError

    def _append( self, todo: Todo ) -> None:
        raise NotImplementedError

    def items( self ) -> list[ Todo ]:
        current: dict[ int, Todo ] = {}
        for todo in self._versions():
            current[ todo.number ] = todo
        return [ current[ n ] for n in sorted( current )]

    def add( self, text: str, by: str ) -> Todo:
        existing = self.items()
        todo = Todo( number=max(( t.number for t in existing ), default=0 ) + 1,
                     text=text.strip(), added_at=datetime.now().astimezone(), added_by=by )
        self._append( todo )
        return todo

    def complete( self, number: int, by: str ) -> Todo:
        todo = next(( t for t in self.items() if t.number == number ), None )
        if todo is None:
            raise KeyError( number )
        if todo.state is TodoState.DONE:
            return todo
        done = todo.model_copy( update={ "state": TodoState.DONE,
                                         "done_at": datetime.now().astimezone(), "done_by": by })
        self._append( done )
        return done


class JsonlTodoStore( _Ledger ):
    def __init__( self, home: Path | None = None ) -> None:
        self._home = home or default_home()
        self._path = self._home / "todos.jsonl"

    @property
    def path( self ) -> Path:
        return self._path

    def _versions( self ) -> list[ Todo ]:
        if not self._path.exists():
            return []
        return [ Todo.model_validate_json( line )
                 for line in self._path.read_text().splitlines() if line.strip()]

    def _append( self, todo: Todo ) -> None:
        self._home.mkdir( mode=0o700, parents=True, exist_ok=True )
        with self._path.open( "a" ) as out:
            out.write( todo.model_dump_json() + "\n" )
            out.flush()
            os.fsync( out.fileno())
        self._path.chmod( 0o600 )


class InMemoryTodoStore( _Ledger ):
    def __init__( self ) -> None:
        self._lines: list[ str ] = []

    def _versions( self ) -> list[ Todo ]:
        return [ Todo.model_validate( json.loads( line )) for line in self._lines ]

    def _append( self, todo: Todo ) -> None:
        self._lines.append( todo.model_dump_json())

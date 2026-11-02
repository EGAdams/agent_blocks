"""IAskPerson over the company to-do list: a question is an item, added by "scala".

EG already reads that list through Toyota, so a question put there is heard
without a new screen. The item's number is the question's.
"""
from __future__ import annotations

from company_todos.interfaces import ITodoStore


class TodoListAsker:
    def __init__( self, store: ITodoStore, who: str = "scala" ) -> None:
        self._store = store
        self._who = who

    def ask( self, text: str ) -> int:
        return self._store.add( text, self._who ).number

    def close( self, number: int ) -> None:
        try:
            self._store.complete( number, self._who )
        except KeyError:
            pass                                # already gone from the list: nothing to check off

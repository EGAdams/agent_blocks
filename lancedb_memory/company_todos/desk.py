"""TodoDesk: apply one command to the store and say, in one line, what happened.

The line is the whole reply -- Toyota repeats it verbatim -- so it always says
whether the list changed, and never claims a change the store didn't make.
"""
from __future__ import annotations

import re

from .interfaces import ITodoStore
from .models import Todo, TodoAction, TodoCommand, TodoState


def _words( text: str ) -> set[ str ]:
    return { w for w in re.findall( r"[a-z0-9]+", text.lower()) if len( w ) > 2 }


def _label( todo: Todo ) -> str:
    return f"#{ todo.number } { todo.text }"


class TodoDesk:
    def __init__( self, store: ITodoStore, who: str = "toyota" ) -> None:
        self._store = store
        self._who   = who

    def open_items( self ) -> list[ Todo ]:
        return [ t for t in self._store.items() if t.state is TodoState.OPEN ]

    def handle( self, command: TodoCommand ) -> str:
        if command.action is TodoAction.ADD:
            return self._add( command.text )
        if command.action is TodoAction.DONE:
            return self._done( command )
        if command.action is TodoAction.LIST:
            return self.summary()
        return f"I didn't change the company to-do list: { command.reason }."

    def summary( self ) -> str:
        items = self.open_items()
        done  = len( self._store.items()) - len( items )
        tail  = f" ({ done } done.)" if done else ""
        if not items:
            return "The company to-do list has nothing open." + tail
        listed = "; ".join( _label( t ) for t in items )
        return f"The company to-do list has { len( items )} open item{ 's' if len( items ) != 1 else '' }: { listed }.{ tail }"

    def _add( self, text: str ) -> str:
        for todo in self.open_items():
            if _words( todo.text ) == _words( text ):
                return f"That's already on the company to-do list as { _label( todo )}. Nothing was added."
        todo = self._store.add( text, self._who )
        return f"Added to the company to-do list as { _label( todo )}. { len( self.open_items())} open now."

    def _done( self, command: TodoCommand ) -> str:
        if command.number is not None:
            match = [ t for t in self._store.items() if t.number == command.number ]
            if not match:
                return f"There's no item #{ command.number } on the company to-do list, so nothing was changed."
            return self._complete( match[ 0 ])
        wanted = _words( command.text )
        match  = [ t for t in self.open_items() if wanted and wanted <= _words( t.text )]
        if len( match ) == 1:
            return self._complete( match[ 0 ])
        if not match:
            return f"No open item matches \"{ command.text }\", so nothing was changed. { self.summary()}"
        return ( "More than one item matches, so nothing was changed. Which one: "
                 + "; ".join( _label( t ) for t in match ) + "?" )

    def _complete( self, todo: Todo ) -> str:
        if todo.state is TodoState.DONE:
            return f"{ _label( todo )} was already checked off."
        self._store.complete( todo.number, self._who )
        return f"Checked off { _label( todo )}. { len( self.open_items())} open now."

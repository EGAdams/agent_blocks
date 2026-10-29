"""TableStateMachine: ISessionStateMachine -- the State objects, and which
moves between them are legal. An illegal move is a bug, so it raises."""
from __future__ import annotations

from collections.abc import Iterable

from browser_workflows.errors import BrowserWorkflowError

from ..interfaces.session import ISessionState
from ..models import CashySession, SessionStateName

#: from -> the states it may move to.
MOVES: dict[ SessionStateName, frozenset[ SessionStateName ]] = {
    "idle":                frozenset({ "awaiting_user_login", "ready", "awaiting_user_input", "paused" }),
    "awaiting_user_login": frozenset({ "idle", "ready", "awaiting_user_input", "paused" }),
    "ready":               frozenset({ "idle", "awaiting_user_login", "awaiting_user_input", "paused" }),
    "awaiting_user_input": frozenset({ "idle", "awaiting_user_login", "ready", "paused" }),
    "paused":              frozenset({ "idle", "awaiting_user_login", "ready", "awaiting_user_input" }),
}


class TableStateMachine:
    def __init__( self, states: Iterable[ ISessionState ]) -> None:
        self._states = { s.name: s for s in states }

    def state( self, name: SessionStateName ) -> ISessionState:
        return self._states[ name ]

    def move( self, session: CashySession, to: SessionStateName ) -> None:
        here = session.state
        if to == here:
            return
        if to not in MOVES[ here ]:
            raise BrowserWorkflowError( f"a session cannot go from { here } to { to }" )
        if here == "paused" and to not in ( session.paused_from, "idle" ):
            raise BrowserWorkflowError( f"a paused session resumes where it was ({ session.paused_from }), not { to }" )
        if to == "paused":
            session.paused_from = here
        elif here == "paused":
            session.paused_from = None
        session.state = to

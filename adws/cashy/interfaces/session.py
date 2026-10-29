"""Sessions and their states (GoF State)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..models import CashySession, Intent, IntentKind, SessionStateName

if TYPE_CHECKING:
    from ..turn import Turn


class ISessionStore( Protocol ):
    """Repository: sessions by id. Saves only what the sanitiser lets through."""

    def load( self, session_id: str ) -> CashySession: ...

    def save( self, session: CashySession ) -> None: ...

    def ids( self ) -> list[ str ]: ...


class ISessionSanitizer( Protocol ):
    """Strips what must never be stored: query strings, account numbers, values."""

    def clean( self, session: CashySession ) -> CashySession: ...


class ISessionState( Protocol ):
    """What a sentence means while the session rests in this state."""

    @property
    def name( self ) -> SessionStateName: ...

    def handle( self, intent: Intent, turn: "Turn" ) -> None: ...


class ISessionStateMachine( Protocol ):
    """The legal moves between states, and the State object for each."""

    def state( self, name: SessionStateName ) -> ISessionState: ...

    def move( self, session: CashySession, to: SessionStateName ) -> None: ...


class ICashyAction( Protocol ):
    """Command: one thing a person can ask for, run inside a turn."""

    def run( self, intent: Intent, turn: "Turn" ) -> None: ...


class IActionRegistry( Protocol ):
    def action( self, kind: IntentKind ) -> ICashyAction: ...

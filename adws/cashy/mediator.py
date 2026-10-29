"""CashyAgent: the Mediator a person talks to.

A message is read into intents (one per sentence), and each is handed to
the session's current State, which runs its command. The session is loaded
before and saved (sanitised) after; the browser is released after, whatever
happened. Nothing in a page's HTML can reach here as an instruction: pages
are only ever matched against the person's words.
"""
from __future__ import annotations

from browser_workflows.errors import BrowserWorkflowError

from .interfaces.conversation import IIntentInterpreter
from .interfaces.session import ISessionStore
from .models import CashyReply, Intent, utc_now
from .turn import CashyServices, Turn


class CashyAgent:
    def __init__( self, interpreter: IIntentInterpreter, store: ISessionStore, services: CashyServices ) -> None:
        self._interpreter = interpreter
        self._store       = store
        self._services    = services

    def turn( self, text: str, actor: str = "user", session_id: str = "default" ) -> CashyReply:
        return self.perform( self._interpreter.interpret( text ), actor, session_id )

    def perform( self, intents: list[ Intent ], actor: str = "user", session_id: str = "default" ) -> CashyReply:
        """Run intents already read (a fine-grained ADW builds one directly)."""
        session = self._store.load( session_id )
        turn    = Turn( session, self._services, actor )
        try:
            for intent in intents:
                turn.handle( intent )
                if turn.halted:
                    break
        except BrowserWorkflowError as exc:
            turn.fail( f"Something went wrong in the browser: { exc }" )
            if session.task.status == "running":
                session.task.status, session.task.detail = "failed", str( exc )[ :200 ]
        finally:
            turn.release()
            session.updated_at = utc_now()
            self._store.save( session )
        return CashyReply( lines=turn.lines or [ "OK." ], state=session.state, ok=turn.ok, events=turn.events )

    def session_ids( self ) -> list[ str ]:
        return self._store.ids()

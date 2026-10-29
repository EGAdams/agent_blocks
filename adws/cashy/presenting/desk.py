"""CashyDesk: one conversation, as the dashboard's Input Options box has it.

The dashboard already knows how to talk to Toyota and Sydney -- their
receptionist_team sidecars stream `TurnRecord`s -- so Cashy answers in the
same records rather than the dashboard learning a second shape. The person
typing into the dashboard is the person at the keyboard, so this desk's turns
run as `actor="user"`; an agent reaches her through the post office instead,
where that actor can never approve a consequential step.

Turns are serialized: one browser, one session, one turn at a time.
"""
from __future__ import annotations

import threading
from typing import Any

from receptionist_team.service_models import TurnRecord

from ..interfaces.desk import IDebugChromeProbe, IHandoffDetector
from ..interfaces.session import ISessionStore
from ..mediator import CashyAgent


class CashyDesk:
    def __init__( self, agent: CashyAgent, sessions: ISessionStore, handoffs: IHandoffDetector,
                  chrome: IDebugChromeProbe, session_id: str = "default", port: int = 9222 ) -> None:
        self._agent      = agent
        self._sessions   = sessions
        self._handoffs   = handoffs
        self._chrome     = chrome
        self._session_id = session_id
        self._port       = port
        self._lock       = threading.Lock()

    def turn( self, text: str ) -> list[ TurnRecord ]:
        handoff = self._handoffs.detect( text )
        if handoff is not None:
            return [ TurnRecord( kind="handoff", handoff=handoff.model_dump()),
                     TurnRecord( kind="reply", text=f"Opening { handoff.name }'s window now. "
                                                    "Ask her to put me on when you want me back." ),
                     TurnRecord( kind="result", session=self._session_id )]
        self._lock.acquire()
        try:
            reply = self._agent.turn( text, actor="user", session_id=self._session_id )
        except Exception as exc:                 # a reply, never a dead stream
            return [ TurnRecord( kind="error", text=f"Cashy failed: { type( exc ).__name__ }: { exc }" )]
        finally:
            self._lock.release()
        return [ TurnRecord( kind="reply", text=reply.text ), TurnRecord( kind="result", session=self._session_id )]

    def health( self ) -> dict[ str, Any ]:
        session = self._sessions.load( self._session_id )
        return { "ok": True, "agent": "cashy", "display_name": "Cashy", "session": self._session_id,
                 "state": session.state, "bank": session.bank, "debug_chrome_port": self._port,
                 "debug_chrome": self._chrome.reachable() }

    def compact( self ) -> dict[ str, Any ]:
        """Cashy holds no model conversation to summarize: her session is the
        bank, the page and the task, and those are kept."""
        return { "ok": True, "session": self._session_id, "turns_before": 0,
                 "summary": "Cashy keeps no model conversation; her bank, page and task are kept as they are." }

    def alerts( self ) -> dict[ str, Any ]:
        """She sends no post-office tasks, so nothing of hers can fail unheard."""
        return { "ok": True, "agent": "cashy", "alerts": [] }


class HttpDebugChromeProbe:
    def __init__( self, host: str = "127.0.0.1", port: int = 9222, timeout: float = 1.0 ) -> None:
        self._url     = f"http://{ host }:{ port }/json/version"
        self._timeout = timeout

    def reachable( self ) -> bool:
        import urllib.request
        try:
            response = urllib.request.urlopen( self._url, timeout=self._timeout )
        except Exception:
            return False
        try:
            return response.status == 200
        finally:
            response.close()

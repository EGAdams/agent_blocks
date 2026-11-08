"""ChatGptRelayAgent: the IAgentAdapter that lets ChatGPT and Toyota speak to a ChatGPT session. No model in the path.

The prompt is JSON written by code (ChatGPT turns "ask my other session X" into it; Toyota's plan does the same):

    {"relay": "ask", "instance": "bati", "message": "what did we decide about the plat?"}

    inspect    read_only        instances | health | ready | read
    implement  workspace_write  open | new_chat | type | send | ask            (workspace `chatgpt-relay`)

`ask` is the one to use: it guards the message, remembers the thread as it stands, types, sends, waits for the
reply to stop changing, and reports it. The others exist because when an ask fails, the next move depends on
which half failed -- the message never arrived, or it arrived and nothing came back.

Each task becomes one run of `adws/adw_chatgpt_relay.py` (through `IRelayCommands`), which is the relay ADW: it
has its own execution id (the ADW id), lifecycle log and object trace. This adapter only checks the request
against a fixed table, never lets a value become an argv option, and turns the ADW's one JSON outcome into an
honest AgentOutcome: success means the relay verified the operation, never that it was queued.

Three consequences are enforced further down and reported back verbatim rather than re-decided here:

- there is no `quit`, anywhere in the table. That Chrome is signed in to EG's account, and closing it costs
  the login.
- a message that reads like a password, a PIN or a one-time code is refused before anything is typed. It
  would land in the account's permanent history.
- `new_chat` is refused unless the request carries `"confirm": true`, because it abandons the thread that is
  on screen, and `instance` plus `hops` are what stop two sessions that can message each other from
  ping-ponging forever.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "chatgpt_relay"
READS     = frozenset({ "instances", "health", "ready", "read" })
WRITES    = frozenset({ "open", "new_chat", "type", "send", "ask" })
#: request key -> the ADW flag it becomes (the ADW spells a two-word flag with a dash: --stable-seconds).
FLAGS     = { "instance": "instance", "message": "message", "url": "url", "last": "last", "settle": "settle",
              "timeout": "timeout", "stable_seconds": "stable-seconds", "hops": "hops", "origin": "origin" }
REQUIRED  = { "ask": ( "message", ), "type": ( "message", ) }
#: `confirm` and `no_wait` are the two switches; everything else is short text.
SWITCHES  = ( "confirm", "no_wait" )
MAX_VALUE = 500
#: A message is the one long field. The relay's own LengthGuard holds the real limit; this only keeps a task
#: prompt from carrying a book. It is deliberately under ClaimedTask.prompt's own 8000-character cap, so a
#: too-long message is refused here with a sentence about the message rather than rejected upstream as a
#: malformed task -- a 12000 set here could never be reached and would have been a limit in name only.
MAX_MESSAGE = 7000


class RelayCommandFailed( Exception ):
    """The relay ADW gave no outcome (it crashed, timed out, or wrote nothing)."""


class IRunningRelayCommand( Protocol ):
    def poll( self ) -> dict | None: ...        # None while running; the ADW's outcome once done
    def stop( self ) -> None: ...


class IRelayCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ], switches: frozenset[ str ] ) -> IRunningRelayCommand: ...


class ChatGptRelayAgent:
    def __init__( self, commands: IRelayCommands, clock: IClock, poll_seconds: float = 0.5,
                  deadline_seconds: float = 300.0 ) -> None:
        # The deadline is longer than the browser agent's: an ask waits for a person-sized answer to be
        # written, and the relay's own --timeout (120s by default) is what should end it, not this.
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "relay" ), str ):
            return self._refused( 'a relay task is JSON, e.g. {"relay": "ask", "message": "..."}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "relay" ].removeprefix( "relay." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"the relay does not offer { what!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        unknown = set( request ) - set( FLAGS ) - set( SWITCHES ) - { "relay" }
        if unknown:
            return self._refused( "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, flag in FLAGS.items():
            value = request.get( key )
            if value is None or value == "":
                continue
            limit = MAX_MESSAGE if key == "message" else MAX_VALUE
            if not isinstance( value, ( str, int, float )) or isinstance( value, bool ) or len( str( value )) > limit:
                return self._refused( f"{ key } must be short text" if key != "message"
                                      else f"that message is longer than { MAX_MESSAGE } characters" )
            flags[ flag ] = str( value )
        missing = [ k for k in REQUIRED.get( what, ()) if FLAGS[ k ] not in flags ]
        if missing:
            return self._refused( f"{ what } needs: " + ", ".join( missing ))
        if what == "new_chat" and request.get( "confirm" ) is not True:
            return self._refused( "new_chat leaves the thread on screen behind, so it needs confirm=true, and "
                                  "only after EG said so" )
        switches = frozenset( name for name in SWITCHES if request.get( name ) is True )
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags, switches ), what, keep_alive )
        except RelayCommandFailed as exc:
            return self._failed( f"the relay ADW gave no outcome ({ exc })", what )
        if signal == "lost":
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        return self._outcome( answer, what )

    def _wait( self, running: IRunningRelayCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a message that has been sent is never
        abandoned half way -- the reply is still coming, and the thread is the record of it either way."""
        deadline, seen = self._clock.monotonic() + self._deadline, "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise RelayCommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if what in READS:
                    running.stop()
                    return {}, signal
            self._clock.sleep( self._poll )

    @staticmethod
    def _outcome( answer: dict, what: str ) -> AgentOutcome:
        data = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line = str( answer.get( "line" ) or "" )[ :1000 ]
        # An ask that was not settled is not an answer: the reply was still being written when the deadline
        # won, and reporting it as the reply is how a half-written sentence becomes a decision.
        settled = data.get( "settled" ) is not False
        verified = bool( answer.get( "ok" )) and settled
        details = { "relay": what, "ok": bool( answer.get( "ok" )), "verified": verified,
                    "adw_id": answer.get( "adw_id" ), **data }
        body = line + ( "\n\n" + str( data.get( "reply" )) if data.get( "reply" ) else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body,
                                 details=details )
        # A refusal (a credential, an unknown session, a new chat without a yes) is final: repeating the same
        # request gives the same answer.
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER,
                             summary=( line or "the relay operation did not complete" )[ :300 ],
                             body=body, retryable=False, details=details )

    @staticmethod
    def _failed( summary: str, what: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary,
                             retryable=False, details={ "relay": what, "ok": False, "verified": False } )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )

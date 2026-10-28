"""The mechanic's side of the post office: hear failures, gather the facts, keep cases.

* `FailureForwarder` -- an IEventSubscriber. On every `task.failed` it posts a
  `diagnose` request to the mechanic through the same post office everybody
  else uses (Observer feeding the Mediator), so the mechanic is a participant,
  not a special case. It never forwards the mechanic's own failures, which
  would loop.
* `MailboxDiagnostic` -- IAgentDiagnostic. Pure code: the request, its
  attempts, the error, who performed it, every event, and -- when the task
  ran an ADW -- the phase that failed, read out of that run's events.jsonl.
* `JsonlCaseHistory` -- ICaseHistory. Each diagnosis (and later its
  correction) is one line, so the next failure with the same error can say
  "seen before in case N".
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .models import Diagnosis, TaskEvent, TaskRequest
from .ports import ICaseHistory, IEventLog, IMessageTransport, ITaskDispatcher


class FailureForwarder:

    def __init__( self, dispatcher: ITaskDispatcher, mechanic: str = "mechanic", reporter: str = "post-office" ) -> None:
        self._dispatcher = dispatcher
        self._mechanic   = mechanic
        self._reporter   = reporter

    def notify( self, event: TaskEvent ) -> None:
        if event.name != "task.failed" or event.agent == self._mechanic:
            return
        self._dispatcher.send( TaskRequest(
            sender    = self._reporter,
            recipient = self._mechanic,
            task_type = "diagnose",
            text      = f"Task { event.task_id } failed on { event.agent }: { event.details.get( 'error', '' ) }",
            context   = { "task_id": event.task_id,
                          **({ "adw_id": event.details[ "adw_id" ]} if event.details.get( "adw_id" ) else {})},
            priority  = 20,
            wait_seconds = 0,
        ))


class MailboxDiagnostic:

    def __init__( self, transport: IMessageTransport, events: IEventLog, cases: ICaseHistory,
                  adw_sessions: list[ Path ] | None = None ) -> None:
        self._transport    = transport
        self._events       = events
        self._cases        = cases
        self._adw_sessions = [ Path( p ) for p in ( adw_sessions or [])]

    def diagnose( self, task_id: str, adw_id: str | None = None ) -> Diagnosis:
        found = self._transport.find( task_id )
        if found is None:
            return self._adw_only( task_id, adw_id )
        _, _, envelope = found
        events = self._events.events_for( task_id )
        adw_id = str( envelope.context.get( "adw_id" ) or self._adw_id_from( events ) or "" ) or None
        return Diagnosis(
            task_id       = task_id,
            found         = True,
            sender        = envelope.sender,
            recipient     = envelope.recipient,
            task_type     = envelope.task_type,
            request_text  = envelope.text,
            context       = envelope.context,
            performer     = envelope.performer,
            attempts      = envelope.attempts,
            error         = envelope.error,
            adw_id        = adw_id,
            failed_phase  = self._failed_phase( adw_id ) if adw_id else None,
            events        = events,
            similar_cases = self._cases.similar( envelope.error or "" ),
        )

    def _adw_only( self, task_id: str, adw_id: str | None ) -> Diagnosis:
        """No post-office record -- but a reported ADW failure still has its run."""
        events = self._events.events_for( task_id )
        adw_id = adw_id or self._adw_id_from( events )
        if not adw_id and not events:
            return Diagnosis( task_id=task_id, found=False )
        failed = next(( e for e in reversed( events ) if e.name == "task.failed" ), None )
        error  = str( failed.details.get( "error" )) if failed else None
        return Diagnosis( task_id=task_id, found=True, adw_id=adw_id, events=events,
                          performer=failed.agent if failed else None, error=error,
                          failed_phase=self._failed_phase( adw_id ) if adw_id else None,
                          similar_cases=self._cases.similar( error or "" ))

    @staticmethod
    def _adw_id_from( events: list[ TaskEvent ]) -> str | None:
        for event in reversed( events ):
            if event.details.get( "adw_id" ):
                return str( event.details[ "adw_id" ])
        return None

    def _failed_phase( self, adw_id: str ) -> str | None:
        for sessions in self._adw_sessions:
            log = sessions / adw_id / "events.jsonl"
            if not log.exists():
                continue
            for line in log.read_text( encoding="utf-8" ).splitlines():
                try:
                    event = json.loads( line )
                except ValueError:
                    continue
                failed_end = event.get( "type" ) == "phase_end" and \
                    ( event.get( "payload" ) or {}).get( "status" ) not in ( None, "success" )
                if failed_end or event.get( "type" ) == "error":
                    return _phase_name( adw_id, event )
        return None


class JsonlCaseHistory:

    def __init__( self, path: Path ) -> None:
        self._path = Path( path )

    def record( self, diagnosis: Diagnosis, correction: str = "" ) -> str:
        case_id = f"case-{ uuid.uuid4().hex[ :8 ] }"
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with open( self._path, "a", encoding="utf-8" ) as handle:
            handle.write( json.dumps({ "case_id": case_id, "task_id": diagnosis.task_id,
                                       "recipient": diagnosis.recipient, "error": diagnosis.error,
                                       "failed_phase": diagnosis.failed_phase,
                                       "correction": correction }) + "\n" )
        return case_id

    def similar( self, error: str ) -> list[ str ]:
        if not error or not self._path.exists():
            return []
        key = _error_key( error )
        matches = []
        for line in self._path.read_text( encoding="utf-8" ).splitlines():
            try:
                case = json.loads( line )
            except ValueError:
                continue
            if case.get( "error" ) and _error_key( case[ "error" ]) == key:
                matches.append( case[ "case_id" ])
        return matches


def _phase_name( adw_id: str, event: dict ) -> str:
    """`0559c3e1_05_search_address` -> `search_address`; else the event's name."""
    phase_id = str( event.get( "phase_id" ) or "" )
    prefix   = f"{ adw_id }_"
    if phase_id.startswith( prefix ):
        _, _, name = phase_id[ len( prefix ): ].partition( "_" )
        if name:
            return name
    return str( event.get( "name" ) or phase_id or "unknown" )


def _error_key( error: str ) -> str:
    """The first line, lowercased, with digits blurred -- ids and counts vary."""
    first = error.strip().splitlines()[ 0 ] if error.strip() else ""
    return "".join( "#" if c.isdigit() else c for c in first.lower())[ :160 ]

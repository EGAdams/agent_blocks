"""IRecordingState: Idle -> Recording -> Saving -> Idle.

Each state answers every command; one that makes no sense in it raises
IllegalTransition before touching anything, so a stray "stop" can never
half-change a session.
"""
from __future__ import annotations

from ..errors import IllegalTransition
from ..interfaces.recording import IRecordingSession
from ..models import RawBrowserEvent, RecordingSessionInfo, RecordingStateName, utc_now


class _State:
    name: RecordingStateName

    def start( self, session: IRecordingSession, info: RecordingSessionInfo ) -> "_State":
        raise IllegalTransition( self._refusal( "start recording" ))

    def capture( self, session: IRecordingSession, event: RawBrowserEvent ) -> "_State":
        raise IllegalTransition( self._refusal( "capture an event" ))

    def stop( self, session: IRecordingSession ) -> "_State":
        raise IllegalTransition( self._refusal( "stop recording" ))

    def finish( self, session: IRecordingSession ) -> "_State":
        raise IllegalTransition( self._refusal( "save or discard" ))

    def _refusal( self, what: str ) -> str:
        return f"cannot { what } while { self.name }"


class IdleState( _State ):
    name = "idle"

    def start( self, session: IRecordingSession, info: RecordingSessionInfo ) -> _State:
        session._set_info( info.model_copy( update={ "state": "recording", "started_at": info.started_at or utc_now() }))
        return RECORDING


class RecordingState( _State ):
    name = "recording"

    def capture( self, session: IRecordingSession, event: RawBrowserEvent ) -> _State:
        session._store_event( event )
        return self

    def stop( self, session: IRecordingSession ) -> _State:
        session._set_info( session.info.model_copy( update={ "state": "saving", "ended_at": utc_now() }))
        return SAVING

    def finish( self, session: IRecordingSession ) -> _State:
        # Discarding mid-recording is allowed: it is how a failed start unwinds.
        session._set_info( session.info.model_copy( update={ "state": "idle", "ended_at": utc_now() }))
        return IDLE


class SavingState( _State ):
    name = "saving"

    def finish( self, session: IRecordingSession ) -> _State:
        session._set_info( session.info.model_copy( update={ "state": "idle" }))
        return IDLE


IDLE      = IdleState()
RECORDING = RecordingState()
SAVING    = SavingState()

BY_NAME: dict[ str, _State ] = { s.name: s for s in ( IDLE, RECORDING, SAVING )}

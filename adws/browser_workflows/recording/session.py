"""IRecordingSession: the State pattern's context. Holds the current state and
session, and writes every change through to the session store at once."""
from __future__ import annotations

from ..interfaces.storage import IRecordingSessionStore
from ..models import RawBrowserEvent, RecordingSessionInfo, RecordingStateName
from .states import BY_NAME, IDLE


class StoredRecordingSession:
    def __init__( self, store: IRecordingSessionStore ) -> None:
        self._store  = store
        self._state  = IDLE
        self._info: RecordingSessionInfo | None = None
        self._persisted_id: str | None = None

    @property
    def state( self ) -> RecordingStateName:
        return self._state.name

    @property
    def info( self ) -> RecordingSessionInfo | None:
        return self._info

    def begin( self, info: RecordingSessionInfo ) -> None:
        self._state = self._state.start( self, info )

    def capture( self, event: RawBrowserEvent ) -> None:
        self._state = self._state.capture( self, event )

    def stop( self ) -> None:
        self._state = self._state.stop( self )

    def finish( self ) -> None:
        self._state = self._state.finish( self )

    def restore( self, info: RecordingSessionInfo ) -> None:
        """A service restart cannot pick a recording back up mid-tab, so an
        open session comes back as Saving: its events can still be named."""
        if info.state == "recording":
            info = info.model_copy( update={ "state": "saving" })
            self._store.update( info )
        self._info, self._persisted_id = info, info.id
        self._state = BY_NAME[ info.state ]

    def update_details( self, **fields ) -> None:
        """Tab, url and title arrive after begin(), from the extension."""
        if self._info is not None:
            self._set_info( self._info.model_copy( update=fields ))

    # -- called by the states ------------------------------------------------

    def _set_info( self, info: RecordingSessionInfo | None ) -> None:
        self._info = info
        self._persist()

    def _store_event( self, event: RawBrowserEvent ) -> None:
        self._store.append( self._info.id, event )
        self._info = self._info.model_copy( update={ "event_count": self._info.event_count + 1 })

    def _persist( self ) -> None:
        if self._info is None:
            return
        if self._persisted_id == self._info.id:
            self._store.update( self._info )
        else:
            self._store.open( self._info )
            self._persisted_id = self._info.id

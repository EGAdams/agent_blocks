"""IRecordingController: start, capture, stop, save, discard.

It owns no transport -- the service hands it requests and events, and it
talks to the extension only through IExtensionLink -- and it decides nothing
about what a step is: that is the normaliser's job, and the validator's.
"""
from __future__ import annotations

import uuid

from ..errors import BrowserWorkflowError, ExtensionNotConnected, IllegalTransition
from ..interfaces.normalizing import IActionNormalizer, IEventScrubber
from ..interfaces.playback import IWorkflowValidator
from ..interfaces.recording import IExtensionLink, IRecordingSession
from ..interfaces.storage import IRecordingRepository, IRecordingSessionStore
from ..models import ControllerStatus, RawBrowserEvent, Recording, RecordingSessionInfo, RecordingStep

START_TIMEOUT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS  = 5.0


class RecordingController:
    def __init__( self, session: IRecordingSession, store: IRecordingSessionStore, link: IExtensionLink,
                  scrubber: IEventScrubber, normalizer: IActionNormalizer, validator: IWorkflowValidator,
                  repository: IRecordingRepository ) -> None:
        self._session    = session
        self._store      = store
        self._link       = link
        self._scrubber   = scrubber
        self._normalizer = normalizer
        self._validator  = validator
        self._repository = repository
        leftover = store.latest_open()
        if leftover is not None:
            session.restore( leftover )

    def status( self, message: str = "" ) -> ControllerStatus:
        return ControllerStatus( state=self._session.state, extension_connected=self._link.connected,
                                 session=self._session.info if self._session.state != "idle" else None, message=message )

    async def start( self ) -> ControllerStatus:
        if self._session.state != "idle":
            raise IllegalTransition( f"already { self._session.state }; stop or save that session first" )
        if not self._link.connected:
            raise ExtensionNotConnected( "the Browser Recorder extension is not connected -- is the debug Chrome open "
                                         "with the extension loaded?" )
        session_id = uuid.uuid4().hex[ :12 ]
        # Begin first, so events that race the reply already have a session to land in.
        self._session.begin( RecordingSessionInfo( id=session_id, state="recording" ))
        try:
            reply = await self._link.request({ "type": "start_recording", "session_id": session_id },
                                             "recording_started", START_TIMEOUT_SECONDS )
        except ( TimeoutError, ConnectionError ) as exc:
            self._session.finish()
            raise ExtensionNotConnected( f"the extension did not start recording: { exc or 'no reply' }" ) from exc
        if reply.get( "error" ):
            self._session.finish()
            raise BrowserWorkflowError( f"the extension could not record that tab: { reply[ 'error' ] }" )
        self._session.update_details( tab_id=reply.get( "tab_id" ), start_url=reply.get( "url", "" ),
                                      title=reply.get( "title", "" ))
        return self.status( f"recording tab '{ reply.get( 'title' ) or reply.get( 'url', '' ) }'" )

    def capture( self, session_id: str, event: RawBrowserEvent ) -> bool:
        info = self._session.info
        if info is None or info.id != session_id or self._session.state != "recording":
            return False
        self._session.capture( self._scrubber.scrub( event ))
        return True

    async def stop( self, from_extension: bool = False ) -> ControllerStatus:
        self._session.stop()
        if not from_extension and self._link.connected:
            try:
                await self._link.request({ "type": "stop_recording", "session_id": self._session.info.id },
                                         "recording_stopped", STOP_TIMEOUT_SECONDS )
            except ( TimeoutError, ConnectionError ):
                pass            # Stopped here regardless; the extension stops itself when it hears the state.
        await self._tell_extension()
        return self.status( f"stopped after { self._session.info.event_count } events; name it to save it" )

    async def save( self, name: str, description: str = "" ) -> ControllerStatus:
        if self._session.state != "saving":
            raise IllegalTransition( f"nothing to save while { self._session.state }; stop recording first" )
        info    = self._session.info
        actions = self._normalizer.normalize( self._store.events( info.id ))
        draft   = Recording( id=0, name=name.strip() or "unnamed",
                             steps=[ RecordingStep( id=0, position=i, **a.model_dump()) for i, a in enumerate( actions, 1 )])
        report  = self._validator.validate( draft )
        if not report.ok:
            problems = "; ".join( i.message for i in report.issues if i.severity == "error" )
            raise BrowserWorkflowError( f"not saved: { problems }" )
        recording = self._repository.create( name, actions, description )     # DuplicateName leaves us in Saving
        self._session.update_details( recording_id=recording.id )
        self._session.finish()
        await self._tell_extension( f"saved '{ recording.name }'" )
        return self.status( f"saved '{ recording.name }' (#{ recording.id }) with { len( recording.steps ) } steps" )

    async def discard( self ) -> ControllerStatus:
        if self._session.state == "idle":
            raise IllegalTransition( "nothing to discard" )
        was_recording = self._session.state == "recording"
        self._session.finish()
        if was_recording and self._link.connected:
            await self._link.notify({ "type": "stop_recording", "session_id": self._session.info.id })
        await self._tell_extension( "discarded" )
        return self.status( "discarded" )

    async def extension_said_hello( self, extension_state: str, session_id: str | None ) -> None:
        """The extension (re)connected. If it thinks it is recording a session
        this service is not, tell it to stop: the service is the authority."""
        info = self._session.info
        if extension_state == "recording" and not ( info and info.id == session_id and self._session.state == "recording" ):
            await self._link.notify({ "type": "stop_recording", "session_id": session_id })
        await self._tell_extension()

    async def _tell_extension( self, message: str = "" ) -> None:
        if self._link.connected:
            info = self._session.info
            await self._link.notify({ "type": "state", "state": self._session.state, "message": message,
                                      "session_id": info.id if info else None,
                                      "event_count": info.event_count if info else 0 })

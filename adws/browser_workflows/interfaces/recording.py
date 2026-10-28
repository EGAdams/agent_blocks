"""Recording: the controller, its session state, and the link to the extension."""
from __future__ import annotations

from typing import Any, Protocol

from ..models import ControllerStatus, RawBrowserEvent, RecordingSessionInfo, RecordingStateName


class IRecordingState( Protocol ):
    """State pattern: what each recording command means in this state.
    A command that makes no sense here raises IllegalTransition."""

    @property
    def name( self ) -> RecordingStateName: ...

    def start( self, session: "IRecordingSession", info: RecordingSessionInfo ) -> "IRecordingState": ...

    def capture( self, session: "IRecordingSession", event: RawBrowserEvent ) -> "IRecordingState": ...

    def stop( self, session: "IRecordingSession" ) -> "IRecordingState": ...

    def finish( self, session: "IRecordingSession" ) -> "IRecordingState": ...


class IRecordingSession( Protocol ):
    """The State pattern's context: the session being recorded, if any."""

    @property
    def state( self ) -> RecordingStateName: ...

    @property
    def info( self ) -> RecordingSessionInfo | None: ...

    def begin( self, info: RecordingSessionInfo ) -> None: ...

    def capture( self, event: RawBrowserEvent ) -> None: ...

    def stop( self ) -> None: ...

    def finish( self ) -> None: ...

    def update_details( self, **fields: Any ) -> None:
        """Tab, url and title arrive after begin(), from the extension."""
        ...

    def restore( self, info: RecordingSessionInfo ) -> None:
        """Resume a session the store still has open (after a service restart)."""
        ...

    # Used only by the states themselves.
    def _set_info( self, info: RecordingSessionInfo | None ) -> None: ...

    def _store_event( self, event: RawBrowserEvent ) -> None: ...

    def _persist( self ) -> None: ...


class IExtensionLink( Protocol ):
    """The one connected extension, as the controller sees it."""

    @property
    def connected( self ) -> bool: ...

    async def request( self, message: dict[ str, Any ], reply_type: str,
                       timeout_seconds: float ) -> dict[ str, Any ]:
        """Send a message and wait for the reply of `reply_type`. Raises
        TimeoutError if none comes, ConnectionError if nothing is connected."""
        ...

    async def notify( self, message: dict[ str, Any ]) -> None: ...


class IRecordingController( Protocol ):
    """Starts, stops and saves recording sessions. Owns no transport."""

    def status( self ) -> ControllerStatus: ...

    async def start( self ) -> ControllerStatus: ...

    def capture( self, session_id: str, event: RawBrowserEvent ) -> bool: ...

    async def stop( self, from_extension: bool = False ) -> ControllerStatus: ...

    async def save( self, name: str, description: str = "" ) -> ControllerStatus: ...

    async def discard( self ) -> ControllerStatus: ...


class IRequestGuard( Protocol ):
    """Why a request to the local service must be refused, or None.
    Separate from the routes so the rules can be read, and tested, alone."""

    def control_problem( self, host: str, origin: str | None, content_type: str, has_body: bool ) -> str | None: ...

    def extension_problem( self, host: str, origin: str | None ) -> str | None: ...

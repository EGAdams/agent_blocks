"""Recording, through the recorder service's control API."""
from __future__ import annotations

import json
from typing import Any

from ..interfaces.normalizing import IActionNormalizer, IEventScrubber
from ..interfaces.presenting import IRecorderClient, IServiceLauncher
from ..models import ControllerStatus, LocatorCandidate, RawBrowserEvent
from .common import Operation, OperationOutcome, ok, refused

NOT_RUNNING = "the recorder service is not running; start it with adw_browser_start_recording.py"


def _said( status: ControllerStatus ) -> str:
    s = status.session
    where = f" on { s.start_url }" if s and s.start_url else ""
    count = f", { s.event_count } actions captured" if s and s.state != "idle" else ""
    extension = "" if status.extension_connected else " (the Chrome extension is not connected)"
    return f"recorder { status.state }{ where }{ count }{ extension }{ ': ' + status.message if status.message else '' }"


def _data( status: ControllerStatus ) -> dict[ str, Any ]:
    return { "recorder": status.model_dump()}


class RecorderStatus( Operation ):
    name        = "recorder_status"
    description = "Say whether the recorder is idle, recording or waiting for a name"

    def __init__( self, client: IRecorderClient ) -> None:
        self._client = client

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        status = self._client.status()
        return ok( _said( status ), **_data( status )) if status else refused( NOT_RUNNING )


class StartRecording( Operation ):
    name        = "start_recording"
    description = "Start the recorder service if needed, then record the active Chrome tab"

    def __init__( self, client: IRecorderClient, launcher: IServiceLauncher ) -> None:
        self._client   = client
        self._launcher = launcher

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        problem = self._launcher.ensure_running()
        if problem:
            return refused( problem )
        status = self._client.start()
        return ( ok if status.state == "recording" else refused )( _said( status ), **_data( status ))


class StopRecording( Operation ):
    name        = "stop_recording"
    description = "Stop recording; the captured actions wait to be saved under a name"

    def __init__( self, client: IRecorderClient ) -> None:
        self._client = client

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        current = self._client.status()
        if current is None:
            return refused( NOT_RUNNING )
        if current.state == "saving":                       # stopped already (from the extension's popup, say)
            return ok( "already stopped; it is waiting to be saved under a name", **_data( current ))
        status = self._client.stop()
        return ( ok if status.state == "saving" else refused )( _said( status ), **_data( status ))


class SaveRecording( Operation ):
    name        = "save_recording"
    description = "Save the stopped recording as a named playback"

    def __init__( self, client: IRecorderClient ) -> None:
        self._client = client

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        name = str( args.get( "name" ) or "" ).strip()
        if not name:
            return refused( "a playback needs a name (--name)" )
        if self._client.status() is None:
            return refused( NOT_RUNNING )
        status = self._client.save( name, str( args.get( "description" ) or "" ))
        if status.state != "idle":
            return refused( _said( status ), **_data( status ))
        return ok( f"saved playback '{ name }'. { status.message }".strip(), name=name, **_data( status ))


class DiscardRecording( Operation ):
    name        = "discard_recording"
    description = "Throw away the stopped recording without saving it"

    def __init__( self, client: IRecorderClient ) -> None:
        self._client = client

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        if self._client.status() is None:
            return refused( NOT_RUNNING )
        status = self._client.discard()
        return ( ok if status.state == "idle" else refused )( "recording discarded", **_data( status ))


class CaptureClick( Operation ):
    name        = "capture_click"
    description = "Add one click to the recording in progress, as if the page had seen it"

    def __init__( self, client: IRecorderClient ) -> None:
        self._client = client

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        event = _event( "click", args, text=str( args.get( "text" ) or "" ), tag=str( args.get( "tag" ) or "button" ))
        status = self._client.capture( event )
        return ( ok if status.message == "captured" else refused )( f"click { status.message }", **_data( status ))


class CaptureText( Operation ):
    """The service scrubs the value if the field looks sensitive, as it does
    for the extension's events; a secret never reaches the database."""
    name        = "capture_text"
    description = "Add one completed text entry to the recording in progress"
    redact      = ( "value", )

    def __init__( self, client: IRecorderClient ) -> None:
        self._client = client

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        event = _event( "text_committed", args, value=str( args.get( "value" ) or "" ), tag="input",
                        input_type=str( args.get( "input_type" ) or "text" ))
        status = self._client.capture( event )
        return ( ok if status.message == "captured" else refused )( f"text entry { status.message }", **_data( status ))


def _event( kind: str, args: dict[ str, Any ], **fields: Any ) -> dict[ str, Any ]:
    locators = []
    if args.get( "css" ):
        locators.append( LocatorCandidate( strategy="css", value=str( args[ "css" ]), source="given", rank=20 ))
    if args.get( "label" ):
        locators.append( LocatorCandidate( strategy="label", value=str( args[ "label" ]), source="given", rank=30 ))
    if args.get( "text" ):
        locators.append( LocatorCandidate( strategy="text", value=str( args[ "text" ]), source="given", rank=40 ))
    hints = { "name": str( args.get( "field_name" ) or "" ), "label": str( args.get( "label" ) or "" )}
    return { "kind": kind, "url": str( args.get( "url" ) or "" ), "locators": [ l.model_dump() for l in locators ],
             "field_hints": { k: v for k, v in hints.items() if v }, **fields }


class NormalizeEvent( Operation ):
    """Offline: what one raw event (or a JSON list of them) would be saved as."""
    name        = "normalize_event"
    description = "Show the playback steps raw browser events normalise to, secrets removed"

    def __init__( self, scrubber: IEventScrubber, normalizer: IActionNormalizer ) -> None:
        self._scrubber   = scrubber
        self._normalizer = normalizer

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        raw    = args.get( "event" )
        raw    = json.loads( raw ) if isinstance( raw, str ) else raw
        events = [ self._scrubber.scrub( RawBrowserEvent.model_validate( e )) for e in ( raw if isinstance( raw, list ) else [ raw ])]
        actions = self._normalizer.normalize( events )
        kinds   = ", ".join( a.action_type for a in actions ) or "nothing"
        return ok( f"{ len( events )} event(s) normalise to: { kinds }",
                   actions=[ a.model_dump( exclude={ "recorded_at" }) for a in actions ])

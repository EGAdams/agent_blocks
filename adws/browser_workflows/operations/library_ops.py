"""The saved playbacks as a library: list, load, validate, edit, and the one
entry point agents use -- a free-text request matched to a playback name."""
from __future__ import annotations

import json
import re
from typing import Any

from ..errors import BrowserWorkflowError
from ..interfaces.editing import IRecordingEditor
from ..interfaces.playback import IWorkflowValidator
from ..interfaces.presenting import IPlaybackMatcher
from ..interfaces.storage import IPlaybackRepository
from ..models import RecordedAction, WaitCondition
from .common import Operation, OperationOutcome, locators_from, ok, refused


class _Named( Operation ):
    def __init__( self, repository: IPlaybackRepository ) -> None:
        self._repository = repository

    def recording( self, args: dict[ str, Any ]):
        found = self._repository.by_name( str( args.get( "name" ) or "" ))
        if found is None:
            raise BrowserWorkflowError( f"no playback called { args.get( 'name' )!r}" )
        return found


class ListRecordings( Operation ):
    name        = "list_recordings"
    description = "Every saved playback, by name"

    def __init__( self, repository: IPlaybackRepository ) -> None:
        self._repository = repository

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        found = self._repository.summaries()
        if not found:
            return ok( "no saved playbacks yet", playbacks=[])
        lines = [ f"{ s.name } ({ s.step_count } steps) { s.start_url }".rstrip() for s in found ]
        return ok( "\n".join( lines ), playbacks=[ s.model_dump() for s in found ])


class LoadRecording( _Named ):
    name        = "load_recording"
    description = "One saved playback's steps, secrets shown only as asked-at-playback"

    def __init__( self, repository: IPlaybackRepository, editor: IRecordingEditor ) -> None:
        super().__init__( repository )
        self._editor = editor

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        recording = self.recording( args )
        rows      = self._editor.describe( recording.id )
        lines     = [ f"'{ recording.name }', { len( rows )} steps" ]
        lines    += [ "  " + "  ".join( str( v ) for v in row.values()) for row in rows ]
        return ok( "\n".join( lines ), recording_id=recording.id, steps=rows )


class ValidateRecording( _Named ):
    name        = "validate_recording"
    description = "Check a saved playback can run before running it"

    def __init__( self, repository: IPlaybackRepository, validator: IWorkflowValidator ) -> None:
        super().__init__( repository )
        self._validator = validator

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        recording = self.recording( args )
        report    = self._validator.validate( recording )
        issues    = [ f"step { i.position }: { i.severity }: { i.message }" if i.position else f"{ i.severity }: { i.message }"
                      for i in report.issues ]
        line      = f"'{ recording.name }' is { 'valid' if report.ok else 'NOT valid' }"
        line     += ( "\n  " + "\n  ".join( issues )) if issues else ""
        return ( ok if report.ok else refused )( line, issues=[ i.model_dump() for i in report.issues ])


class EditPlayback( _Named ):
    """One operation, one sub-command per edit (--edit rename|duplicate|...).
    Every change goes through IRecordingEditor, which owns the rules."""
    name        = "edit_playback"
    description = "Rename, duplicate, delete, reorder or change one step of a saved playback"
    redact      = ( "value", )

    def __init__( self, repository: IPlaybackRepository, editor: IRecordingEditor ) -> None:
        super().__init__( repository )
        self._editor = editor

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        recording = self.recording( args )
        rid, pos  = recording.id, int( args.get( "position" ) or 0 )
        edit      = str( args.get( "edit" ) or "" )
        if edit == "rename":
            return ok( f"renamed to '{ self._editor.rename( rid, str( args.get( 'new_name' ) or '' )).name }'" )
        if edit == "duplicate":
            return ok( f"copied to '{ self._editor.duplicate( rid, str( args.get( 'new_name' ) or '' )).name }'" )
        if edit == "delete":
            gone = self._editor.delete( rid, bool( args.get( "yes" )))
            return ok( f"deleted '{ recording.name }'" ) if gone else refused( "deleting needs --yes" )
        if edit == "move":
            self._editor.move_step( rid, pos, int( args.get( "to" ) or 0 ))
        elif edit == "remove":
            self._editor.remove_step( rid, pos )
        elif edit == "insert":
            action = RecordedAction.model_validate( json.loads( str( args.get( "action" ) or "{}" )))
            self._editor.insert_step( rid, pos, action )
        elif edit == "locators":
            self._editor.set_locators( rid, pos, locators_from( args ))
        elif edit == "value":
            self._editor.set_value( rid, pos, str( args.get( "value" ) or "" ))
        elif edit == "waits":
            waits = [ WaitCondition.model_validate( w ) for w in json.loads( str( args.get( "waits" ) or "[]" ))]
            self._editor.set_waits( rid, pos, waits )
        elif edit == "approval":
            self._editor.set_approval( rid, pos, str( args.get( "required" ) or "yes" ).lower() in ( "yes", "true", "1" ))
        elif edit == "url":
            self._editor.set_url( rid, pos, str( args.get( "url" ) or "" ))
        else:
            return refused( "edit must be one of rename, duplicate, delete, move, remove, insert, locators, "
                            "value, waits, approval, url" )
        return ok( f"'{ recording.name }': { edit } done", steps=self._editor.describe( rid ))


# The post office passes the whole routed sentence ("ask the browser recorder
# which playbacks there are"), so the list question can sit anywhere in it.
_LIST_QUESTION = re.compile( r"^\s*list\b|\b(list|which|what|show|name)\b.*\bplaybacks\b", re.I )


def _asks_for_the_list( text: str ) -> bool:
    return bool( _LIST_QUESTION.search( text ))


class AgentRequest( Operation ):
    """What Toyota, Sydney and the Mechanic call. The request picks a
    playback by name; the run gets no approvals and no secrets, so anything
    consequential or secret blocks and says so instead of guessing."""
    name        = "agent_request"
    description = "Run the saved playback a free-text request names (agent-safe)"

    def __init__( self, repository: IPlaybackRepository, matcher: IPlaybackMatcher, playback: Operation ) -> None:
        self._repository = repository
        self._matcher    = matcher
        self._playback   = playback

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        text = str( args.get( "request" ) or "" ).strip()
        if not text:
            return refused( "say which playback to run" )
        summaries = self._repository.summaries()
        if _asks_for_the_list( text ):
            names = ", ".join( s.name for s in summaries ) or "none yet"
            return ok( f"saved playbacks: { names }" )
        chosen, why = self._matcher.match( text, summaries )
        if chosen is None:
            return refused( why )
        safe = { "name": chosen.name, "actor": str( args.get( "actor" ) or "agent" ), "port": args.get( "port" )}
        return self._playback.run( safe )

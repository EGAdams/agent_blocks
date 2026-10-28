"""IRecordingEditor over IRecordingRepository: each edit loads the playback,
changes one thing, and writes it back. Steps keep their ids across moves, so
run history and repairs stay attached to the step they were about."""
from __future__ import annotations

from typing import Any

from ..errors import BrowserWorkflowError, NotFound
from ..interfaces.storage import IRecordingRepository
from ..models import LocatorCandidate, Recording, RecordedAction, RecordingStep, WaitCondition


class RepositoryRecordingEditor:
    def __init__( self, repository: IRecordingRepository ) -> None:
        self._repo = repository

    def rename( self, recording_id: int, name: str ) -> Recording:
        if not name.strip():
            raise BrowserWorkflowError( "a playback needs a name" )
        self._repo.rename( recording_id, name.strip())
        return self._load( recording_id )

    def duplicate( self, recording_id: int, name: str ) -> Recording:
        source  = self._load( recording_id )
        actions = [ RecordedAction.model_validate( s.model_dump( exclude={ "id", "position" })) for s in source.steps ]
        return self._repo.create( name.strip(), actions, source.description )

    def delete( self, recording_id: int, confirm: bool ) -> bool:
        self._load( recording_id )
        if not confirm:
            return False
        self._repo.delete( recording_id )
        return True

    def move_step( self, recording_id: int, position: int, new_position: int ) -> Recording:
        steps = self._load( recording_id ).steps
        step  = steps.pop( self._index( steps, position ))
        steps.insert( max( 0, min( len( steps ), new_position - 1 )), step )
        return self._repo.replace_steps( recording_id, steps )

    def remove_step( self, recording_id: int, position: int ) -> Recording:
        steps = self._load( recording_id ).steps
        steps.pop( self._index( steps, position ))
        return self._repo.replace_steps( recording_id, steps )

    def insert_step( self, recording_id: int, position: int, action: RecordedAction ) -> Recording:
        steps: list[ RecordedAction ] = list( self._load( recording_id ).steps )
        steps.insert( max( 0, min( len( steps ), position - 1 )), action )
        return self._repo.replace_steps( recording_id, steps )

    def set_locators( self, recording_id: int, position: int, locators: list[ LocatorCandidate ]) -> Recording:
        if not locators:
            raise BrowserWorkflowError( "a step needs at least one locator" )
        return self._change( recording_id, position, locators=locators )

    def set_value( self, recording_id: int, position: int, value: str ) -> Recording:
        step = self._step( recording_id, position )
        if step.value_is_secret:
            raise BrowserWorkflowError( f"step { position }'s value is a secret; it is asked for at playback, never stored" )
        if step.action_type not in ( "enter_text", "select_option", "set_checked", "press_key" ):
            raise BrowserWorkflowError( f"a { step.action_type } step has no value" )
        return self._change( recording_id, position, value=value )

    def set_waits( self, recording_id: int, position: int, waits: list[ WaitCondition ]) -> Recording:
        return self._change( recording_id, position, wait_after=waits )

    def set_approval( self, recording_id: int, position: int, required: bool ) -> Recording:
        return self._change( recording_id, position, requires_approval=required )

    def set_url( self, recording_id: int, position: int, url: str ) -> Recording:
        if not url.startswith(( "http://", "https://" )):
            raise BrowserWorkflowError( "a URL must start with http:// or https://" )
        return self._change( recording_id, position, url=url )

    def describe( self, recording_id: int ) -> list[ dict[ str, Any ]]:
        return [{ "position": s.position, "action": s.action_type, "url": s.url,
                  "value": "(secret, asked at playback)" if s.value_is_secret else s.value,
                  "approval": s.requires_approval, "locators": [ f"{ c.strategy }={ c.value }" for c in s.locators ],
                  "waits": [ f"{ w.kind } { w.value }".strip() for w in s.wait_after ],
                  "text": s.metadata.get( "text", "" )} for s in self._load( recording_id ).steps ]

    # -- helpers -------------------------------------------------------------

    def _load( self, recording_id: int ) -> Recording:
        recording = self._repo.by_id( recording_id )
        if recording is None:
            raise NotFound( f"no playback #{ recording_id }" )
        return recording

    def _step( self, recording_id: int, position: int ) -> RecordingStep:
        steps = self._load( recording_id ).steps
        return steps[ self._index( steps, position )]

    def _change( self, recording_id: int, position: int, **fields: Any ) -> Recording:
        self._repo.update_step( self._step( recording_id, position ).model_copy( update=fields ))
        return self._load( recording_id )

    @staticmethod
    def _index( steps: list[ RecordingStep ], position: int ) -> int:
        for i, step in enumerate( steps ):
            if step.position == position:
                return i
        raise NotFound( f"no step { position }; the playback has { len( steps )}" )

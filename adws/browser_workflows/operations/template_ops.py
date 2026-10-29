"""Turning a recording of one month into a playback for any month."""
from __future__ import annotations

from typing import Any

from ..interfaces.editing import IRecordingEditor
from ..interfaces.storage import IPlaybackRepository
from ..interfaces.templating import IPeriodParser, IPeriodVocabulary, IPlaybackParameterizer, IRecordingBinder
from ..models import LocatorCandidate
from .common import Operation, OperationOutcome, ok, refused


def _locators( found: list[ LocatorCandidate ]) -> str:
    return "; ".join( f"{ c.strategy }={ c.value }" for c in found ) or "(none)"


class ParameterizePlayback( Operation ):
    """Shows the proposal; --yes saves it as a new playback (--new-name).
    The recording itself is never changed."""
    name        = "parameterize_playback"
    description = "Propose (and with --yes save) a copy of a playback with its month and year as blanks"

    def __init__( self, repository: IPlaybackRepository, editor: IRecordingEditor, parameterizer: IPlaybackParameterizer,
                  parser: IPeriodParser, vocabulary: IPeriodVocabulary, binder: IRecordingBinder ) -> None:
        self._repository    = repository
        self._editor        = editor
        self._parameterizer = parameterizer
        self._parser        = parser
        self._vocabulary    = vocabulary
        self._binder        = binder

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        recording = self._repository.by_name( str( args.get( "name" ) or "" ))
        if recording is None:
            return refused( f"no playback called { args.get( 'name' )!r}" )
        period = self._parser.parse( str( args.get( "example" ) or recording.name.replace( "_", " " )))
        if period is None:
            return refused( "which month did this recording fetch? say --example \"September 2025\"" )
        changes = self._parameterizer.propose( recording, self._vocabulary.values( period ))
        if not changes:
            return refused( f"nothing in '{ recording.name }' names { self._vocabulary.said( period )}; nothing to template" )
        new_name = str( args.get( "new_name" ) or "" ).strip()
        lines    = [ f"'{ recording.name }' recorded { self._vocabulary.said( period )}; proposed template:" ]
        for c in changes:
            lines += [ f"  step { c.position }: { _locators( c.before )}", f"       -> { _locators( c.after )}" ]
            lines += [ f"       ({ note })" for note in c.notes ]
        if not ( args.get( "yes" ) and new_name ):
            lines.append( "nothing saved; run again with --new-name NAME --yes to save it as a new playback" )
            return ok( "\n".join( lines ), changes=[ c.model_dump() for c in changes ], saved=False )
        copy = self._editor.duplicate( recording.id, new_name )
        for c in changes:
            copy = self._editor.set_locators( copy.id, c.position, c.after )
        blanks = ", ".join( self._binder.placeholders( copy ))
        lines.append( f"saved as '{ copy.name }'; it needs { blanks } (e.g. --period \"October 2025\")" )
        return ok( "\n".join( lines ), changes=[ c.model_dump() for c in changes ], saved=True, name=copy.name )

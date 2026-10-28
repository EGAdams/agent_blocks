"""IPlaybackMenuProvider over the recording repository, read fresh each call,
so a playback saved a moment ago is already there."""
from __future__ import annotations

from ..interfaces.presenting import PlaybackMenuEntry
from ..interfaces.storage import IPlaybackRepository


class RepositoryPlaybackMenu:
    def __init__( self, repository: IPlaybackRepository ) -> None:
        self._repository = repository

    def entries( self ) -> list[ PlaybackMenuEntry ]:
        return [ PlaybackMenuEntry( title=f"{ s.name } ({ s.step_count } steps)", recording_id=s.id, name=s.name )
                 for s in self._repository.summaries()]

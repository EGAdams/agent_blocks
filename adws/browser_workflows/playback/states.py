"""IPlaybackState: the run's State machine, as a transition table.

  ready   --start-->  playing
  playing --pause-->  paused   --resume--> playing
  playing --complete|fail|block|stop--> completed|failed|blocked|stopped
  paused  --stop|fail--> stopped|failed
"""
from __future__ import annotations

from ..errors import IllegalTransition
from ..models import PlaybackStateName

TRANSITIONS: dict[ str, dict[ str, PlaybackStateName ]] = {
    "ready":   { "start": "playing", "fail": "failed" },
    "playing": { "pause": "paused", "complete": "completed", "fail": "failed", "block": "blocked", "stop": "stopped" },
    "paused":  { "resume": "playing", "stop": "stopped", "fail": "failed" },
}


class TablePlaybackState:
    def __init__( self, name: PlaybackStateName = "ready" ) -> None:
        self._name = name

    @property
    def name( self ) -> PlaybackStateName:
        return self._name

    @property
    def finished( self ) -> bool:
        return self._name not in TRANSITIONS

    def on( self, event: str ) -> "TablePlaybackState":
        target = TRANSITIONS.get( self._name, {}).get( event )
        if target is None:
            raise IllegalTransition( f"a { self._name } run cannot { event }" )
        return TablePlaybackState( target )

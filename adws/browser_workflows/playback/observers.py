"""IExecutionObserver implementations: the run log, the console, the
structured log, and a Composite that tells all of them."""
from __future__ import annotations

from ..interfaces.playback import IExecutionObserver
from ..interfaces.presenting import IConsole
from ..interfaces.storage import IPlaybackRunLog
from ..models import PlaybackStateName, Recording, StepResult
from ..structured_log import IStructuredLog


class RunLogObserver:
    def __init__( self, run_log: IPlaybackRunLog ) -> None:
        self._log = run_log

    def run_started( self, run_id: int, recording: Recording ) -> None:
        pass                                    # the executor opened the run to get its id

    def step_finished( self, result: StepResult ) -> None:
        self._log.record( result )

    def state_changed( self, run_id: int, state: PlaybackStateName ) -> None:
        pass

    def run_finished( self, run_id: int, state: PlaybackStateName, message: str ) -> None:
        self._log.finish( run_id, state, message )


class ConsoleObserver:
    MARKS = { "ok": "✓", "failed": "✗", "blocked": "■", "skipped": "·" }

    def __init__( self, console: IConsole ) -> None:
        self._console = console

    def run_started( self, run_id: int, recording: Recording ) -> None:
        self._console.say( f"Playing '{ recording.name }' ({ len( recording.steps )} steps), run #{ run_id }" )

    def step_finished( self, result: StepResult ) -> None:
        mark = self.MARKS.get( result.status, "?" )
        self._console.say( f"  { mark } { str( result.position ).rjust( 3 )} { result.action_type.ljust( 13 )} { result.message }" )
        if result.diagnostics and result.diagnostics.repair_note:
            self._console.say( f"        { result.diagnostics.repair_note }" )

    def state_changed( self, run_id: int, state: PlaybackStateName ) -> None:
        if state == "paused":
            self._console.say( "  paused -- set the run's control to 'run' to carry on, or 'stop'" )

    def run_finished( self, run_id: int, state: PlaybackStateName, message: str ) -> None:
        self._console.say( f"Run #{ run_id } { state }: { message }" )


class StructuredLogObserver:
    def __init__( self, log: IStructuredLog ) -> None:
        self._log = log

    def run_started( self, run_id: int, recording: Recording ) -> None:
        self._log.write( "run_started", run_id=run_id, recording=recording.name, steps=len( recording.steps ))

    def step_finished( self, result: StepResult ) -> None:
        self._log.write( "step_finished", run_id=result.run_id, position=result.position, action=result.action_type,
                         status=result.status, strategy=result.strategy, message=result.message )

    def state_changed( self, run_id: int, state: PlaybackStateName ) -> None:
        self._log.write( "state_changed", run_id=run_id, state=state )

    def run_finished( self, run_id: int, state: PlaybackStateName, message: str ) -> None:
        self._log.write( "run_finished", run_id=run_id, state=state, message=message )


class CompositeObserver:
    """One observer that is many; one that fails does not stop the others or the run."""

    def __init__( self, observers: list[ IExecutionObserver ]) -> None:
        self._observers = observers

    def run_started( self, run_id: int, recording: Recording ) -> None:
        self._each( "run_started", run_id, recording )

    def step_finished( self, result: StepResult ) -> None:
        self._each( "step_finished", result )

    def state_changed( self, run_id: int, state: PlaybackStateName ) -> None:
        self._each( "state_changed", run_id, state )

    def run_finished( self, run_id: int, state: PlaybackStateName, message: str ) -> None:
        self._each( "run_finished", run_id, state, message )

    def _each( self, method: str, *args ) -> None:
        for observer in self._observers:
            try:
                getattr( observer, method )( *args )
            except Exception:
                pass

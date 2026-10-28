"""IPlaybackExecutor: a whole recording, step by step, through one browser.

Validates first; opens the run; asks the control between steps (run, pause,
stop); hands each step to the step runner; stops at the first failed or
blocked step and says where to resume. Every change goes to the observers.
"""
from __future__ import annotations

from browser_session.interfaces.system import IClock

from ..interfaces.browser import IBrowserLease
from ..interfaces.playback import IExecutionObserver, IPlaybackControl, IStepRunner, IWorkflowValidator
from ..interfaces.storage import IPlaybackRunLog
from ..models import PlaybackStateName, Recording
from .states import TablePlaybackState


class SequentialPlaybackExecutor:
    def __init__( self, lease: IBrowserLease, runner: IStepRunner, validator: IWorkflowValidator,
                  run_log: IPlaybackRunLog, control: IPlaybackControl, observer: IExecutionObserver,
                  clock: IClock, pause_limit_seconds: float = 1800.0, poll_seconds: float = 0.5 ) -> None:
        self._lease       = lease
        self._runner      = runner
        self._validator   = validator
        self._run_log     = run_log
        self._control     = control
        self._observer    = observer
        self._clock       = clock
        self._pause_limit = pause_limit_seconds
        self._poll        = poll_seconds

    def execute( self, recording: Recording, actor: str, from_position: int = 1 ) -> tuple[ int, PlaybackStateName, str ]:
        run_id = self._run_log.start( recording.id, actor )
        state  = TablePlaybackState()
        self._observer.run_started( run_id, recording )

        report = self._validator.validate( recording )
        if not report.ok:
            problems = "; ".join( i.message for i in report.issues if i.severity == "error" )
            return self._finish( run_id, state.on( "fail" ), f"not valid, nothing was run: { problems }" )

        state = self._moved( run_id, state, "start" )
        steps = [ s for s in recording.steps if s.position >= from_position ]
        done  = 0
        for step in steps:
            command = self._control.command( run_id )
            if command == "pause":
                state   = self._moved( run_id, state, "pause" )
                command = self._await_resume( run_id )
                if command == "pause":
                    return self._finish( run_id, state, f"paused too long; resume with --from-step { step.position }" )
                if command == "run":
                    state = self._moved( run_id, state, "resume" )
            if command == "stop":
                return self._finish( run_id, state.on( "stop" ), f"stopped before step { step.position }; "
                                                                 f"resume with --from-step { step.position }" )

            result = self._runner.run( self._lease.current(), step, recording, run_id )
            self._observer.step_finished( result )
            if result.status == "failed":
                return self._finish( run_id, state.on( "fail" ), f"step { step.position } failed: { result.message }; "
                                                                 f"resume with --from-step { step.position }" )
            if result.status == "blocked":
                return self._finish( run_id, state.on( "block" ), f"step { step.position } blocked: { result.message }; "
                                                                  f"resume with --from-step { step.position }" )
            done += 1

        return self._finish( run_id, state.on( "complete" ), f"all { done } steps done" if done else "no steps to run" )

    def _await_resume( self, run_id: int ) -> str:
        deadline = self._clock.now() + self._pause_limit
        while self._clock.now() < deadline:
            self._clock.sleep( self._poll )
            command = self._control.command( run_id )
            if command != "pause":
                return command
        return "pause"

    def _moved( self, run_id: int, state: TablePlaybackState, event: str ) -> TablePlaybackState:
        state = state.on( event )
        self._observer.state_changed( run_id, state.name )
        return state

    def _finish( self, run_id: int, state: TablePlaybackState, message: str ) -> tuple[ int, PlaybackStateName, str ]:
        self._observer.run_finished( run_id, state.name, message )
        return run_id, state.name, message

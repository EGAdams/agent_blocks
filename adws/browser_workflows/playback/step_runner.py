"""IStepRunner: one step, in the order a careful person would do it --
ask before a consequential action, find the element (waiting for it, never
sleeping), act, then wait for what the action should cause. Any failure
becomes a StepResult with its evidence; nothing here raises."""
from __future__ import annotations

from ..errors import StepBlocked
from ..interfaces.browser import IBrowserAdapter, IWorkflowElement
from ..interfaces.locating import IElementWaiter
from ..interfaces.playback import ( IActionCommandFactory, IActionExecutor, IApprovalGate, IConditionWaiter,
                                    IPlaybackRecovery )
from ..models import Recording, RecordingStep, Resolution, StepResult, utc_now

DEFAULT_ELEMENT_TIMEOUT = 15.0


class GuardedStepRunner:
    def __init__( self, commands: IActionCommandFactory, waiter: IElementWaiter, executor: IActionExecutor,
                  conditions: IConditionWaiter, approvals: IApprovalGate, recovery: IPlaybackRecovery ) -> None:
        self._commands   = commands
        self._waiter     = waiter
        self._executor   = executor
        self._conditions = conditions
        self._approvals  = approvals
        self._recovery   = recovery

    def run( self, browser: IBrowserAdapter, step: RecordingStep, recording: Recording, run_id: int ) -> StepResult:
        started = utc_now()

        def result( status: str, message: str, strategy: str = "", diagnostics=None ) -> StepResult:
            return StepResult( run_id=run_id, step_id=step.id, position=step.position, action_type=step.action_type,
                               status=status, strategy=strategy, message=message[ :300 ], diagnostics=diagnostics,
                               started_at=started, ended_at=utc_now())

        def failed( message: str, resolution: Resolution | None ) -> StepResult:
            evidence = self._recovery.recover( browser, step, resolution, message, run_id )
            return result( "failed", message, resolution.winner if resolution else "", evidence )

        if step.requires_approval and not self._approvals.approve( step, recording ):
            return result( "blocked", "consequential step not approved; nothing was done" )

        element: IWorkflowElement | None = None
        resolution: Resolution | None = None
        try:
            if self._commands.create( step ).needs_element:
                element, resolution = self._waiter.wait_for( browser, step.locators, _element_timeout( step ))
                if element is None:
                    return failed( "no recorded locator found exactly one usable element", resolution )
            done = self._executor.execute( browser, step, element )
        except StepBlocked as exc:
            return result( "blocked", str( exc ))
        except Exception as exc:
            return failed( f"{ type( exc ).__name__ }: { _first_line( exc )}", resolution )

        problem = self._conditions.wait( browser, step.wait_after )
        if problem:
            return failed( f"{ done }, but then { problem }", resolution )
        return result( "ok", done, resolution.winner if resolution else "" )


def _element_timeout( step: RecordingStep ) -> float:
    try:
        return float( step.metadata.get( "element_timeout_seconds", DEFAULT_ELEMENT_TIMEOUT ))
    except ( TypeError, ValueError ):
        return DEFAULT_ELEMENT_TIMEOUT


def _first_line( exc: Exception ) -> str:
    text = str( exc ).strip()
    return text.splitlines()[ 0 ] if text else "no detail"

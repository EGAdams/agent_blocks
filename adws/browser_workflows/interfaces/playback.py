"""Playing a recording back: commands, gates, observers, control, recovery."""
from __future__ import annotations

from typing import Callable, Protocol

from ..models import ( ElementSummary, PlaybackStateName, Recording, RecordingStep, RepairProposal, Resolution,
                       StepDiagnostics, StepResult, ValidationReport, WaitCondition )
from .browser import IBrowserAdapter, IWorkflowElement


class IActionCommand( Protocol ):
    """Command: one step's action, ready to run against a page.
    Returns a short description of what it did; raises on failure."""

    @property
    def needs_element( self ) -> bool: ...

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str: ...


#: Builds one action type's command from its step.
IActionCommandBuilder = Callable[[ RecordingStep ], IActionCommand ]


class IActionCommandFactory( Protocol ):
    """Factory Method over action types; a new one is a registration."""

    def register( self, action_type: str, builder: IActionCommandBuilder ) -> None: ...

    def create( self, step: RecordingStep ) -> IActionCommand: ...


class IActionExecutor( Protocol ):
    """Executes one browser action for one step, and nothing around it."""

    def execute( self, browser: IBrowserAdapter, step: RecordingStep,
                 element: IWorkflowElement | None ) -> str: ...


class IConditionWaiter( Protocol ):
    """Waits for explicit conditions about the page. Returns "" when they
    hold, or what did not happen in time."""

    def wait( self, browser: IBrowserAdapter, conditions: list[ WaitCondition ]) -> str: ...


class IApprovalGate( Protocol ):
    """May this consequential step run? A person decides, never a guess."""

    def approve( self, step: RecordingStep, recording: Recording ) -> bool: ...


class ISecretSource( Protocol ):
    """Supplies a secret a step needs at the moment it is needed, or None.
    Whatever it returns is used once and never stored or logged."""

    def secret_for( self, step: RecordingStep ) -> str | None: ...


class IPlaybackControl( Protocol ):
    """Asked between steps: carry on, pause, or stop?"""

    def command( self, run_id: int ) -> str:
        """"run", "pause" or "stop"."""
        ...

    def set( self, run_id: int, command: str ) -> None: ...


class IExecutionObserver( Protocol ):
    """Observer: told about a run as it happens. Never changes it."""

    def run_started( self, run_id: int, recording: Recording ) -> None: ...

    def step_finished( self, result: StepResult ) -> None: ...

    def state_changed( self, run_id: int, state: PlaybackStateName ) -> None: ...

    def run_finished( self, run_id: int, state: PlaybackStateName, message: str ) -> None: ...


class IWorkflowValidator( Protocol ):
    def validate( self, recording: Recording ) -> ValidationReport: ...


class IValidationRule( Protocol ):
    def check( self, recording: Recording, report: ValidationReport ) -> None: ...


class IFailureDiagnoser( Protocol ):
    """Collects the evidence for a failed step, without changing the page."""

    def diagnose( self, browser: IBrowserAdapter, step: RecordingStep, resolution: Resolution | None,
                  error: str, run_id: int ) -> StepDiagnostics: ...


class IRepairProposer( Protocol ):
    """From a failed step's evidence to one proposed locator, or None."""

    def propose( self, step: RecordingStep, diagnostics: StepDiagnostics ) -> RepairProposal | None: ...


class IRepairVerifier( Protocol ):
    """Does the proposal find exactly one usable element on the live page?
    Locates only; never clicks."""

    def verify( self, browser: IBrowserAdapter, proposal: RepairProposal ) -> tuple[ bool, str ]: ...


class IPlaybackRecovery( Protocol ):
    """Coordinates what happens after a step has failed every locator:
    collect evidence, and say how a repair can be made."""

    def recover( self, browser: IBrowserAdapter, step: RecordingStep, resolution: Resolution | None,
                 error: str, run_id: int ) -> StepDiagnostics: ...


class IPlaybackState( Protocol ):
    """State pattern for a run."""

    @property
    def name( self ) -> PlaybackStateName: ...

    def on( self, event: str ) -> "IPlaybackState": ...


class IPlaybackExecutor( Protocol ):
    """Coordinates a whole workflow: every step in order, with its waits, its
    gates, its observers, and recovery when one fails."""

    def execute( self, recording: Recording, actor: str, from_position: int = 1 ) -> tuple[ int, PlaybackStateName, str ]: ...


class IConditionCheck( Protocol ):
    """Strategy: does one kind of wait condition hold right now? Asks once, never waits."""

    def holds( self, browser: IBrowserAdapter, value: str ) -> bool: ...


class IStepRunner( Protocol ):
    """One step, end to end: gate, locate, act, wait. Returns its result; never raises."""

    def run( self, browser: IBrowserAdapter, step: RecordingStep, recording: Recording, run_id: int ) -> StepResult: ...


class ISimilarityRanker( Protocol ):
    """Orders the page's elements by how much each resembles the recorded one."""

    def rank( self, step: RecordingStep, elements: list[ ElementSummary ]) -> list[ tuple[ float, ElementSummary ]]: ...


class IRepairApplier( Protocol ):
    """Writes a verified, approved repair into its step. Refuses anything else."""

    def apply( self, repair_id: int, approved: bool ) -> tuple[ bool, str ]: ...

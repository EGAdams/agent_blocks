"""One action on the live debug Chrome each: locate, click, type, wait,
navigate, or run one saved step. Each opens its own lease and always
releases it -- never by closing the window."""
from __future__ import annotations

from typing import Any, Callable

from ..interfaces.browser import IBrowserAdapter, IBrowserAttachment
from ..interfaces.locating import IElementResolver
from ..interfaces.normalizing import IConsequenceClassifier
from ..interfaces.playback import IApprovalGate, IConditionWaiter, ISecretSource, IStepRunner
from ..interfaces.storage import IPlaybackRepository
from ..models import Recording, RecordedAction, RecordingStep, StepResult, WaitCondition
from .common import Operation, OperationOutcome, endpoint, locators_from, ok, refused
from .gates import FlagGateChooser

RunnerFor = Callable[[ IApprovalGate, ISecretSource ], IStepRunner ]
AD_HOC    = Recording( id=0, name="(ad hoc)" )


class _OnBrowser( Operation ):
    def __init__( self, attachment: IBrowserAttachment ) -> None:
        self._attachment = attachment

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        lease = self._attachment.open( endpoint( args ))
        try:
            return self.on( lease.current(), args )
        finally:
            lease.release()

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        raise NotImplementedError


def _outcome( result: StepResult ) -> OperationOutcome:
    data = { "result": result.model_dump()}
    if result.status == "ok":
        return ok( result.message, **data )
    return refused( f"{ result.status }: { result.message }", **data )


class LocateElement( _OnBrowser ):
    name        = "locate_element"
    description = "Find one element on the live page by its locators; touch nothing"

    def __init__( self, attachment: IBrowserAttachment, resolver: IElementResolver ) -> None:
        super().__init__( attachment )
        self._resolver = resolver

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        element, resolution = self._resolver.resolve( browser, locators_from( args ))
        tried = "; ".join( f"{ a.label } -> { a.outcome }" for a in resolution.attempts )
        if element is None:
            return refused( f"not found ({ tried })", resolution=resolution.model_dump())
        return ok( f"found one <{ element.tag_name }> by { resolution.winner }", resolution=resolution.model_dump())


class _AdHocStep( _OnBrowser ):
    """Runs a one-step action through the same step runner playback uses."""

    def __init__( self, attachment: IBrowserAttachment, runner_for: RunnerFor, gates: FlagGateChooser ) -> None:
        super().__init__( attachment )
        self._runner_for = runner_for
        self._gates      = gates

    def run_step( self, browser: IBrowserAdapter, action: RecordedAction, args: dict[ str, Any ]) -> OperationOutcome:
        step = RecordingStep( id=0, position=0, **action.model_dump())
        return _outcome( self._runner_for( *self._gates.gates( args )).run( browser, step, AD_HOC, 0 ))


class ExecuteClick( _AdHocStep ):
    name        = "execute_click"
    description = "Click one element; a consequential-looking one needs --yes"

    def __init__( self, attachment: IBrowserAttachment, runner_for: RunnerFor, gates: FlagGateChooser,
                  classifier: IConsequenceClassifier ) -> None:
        super().__init__( attachment, runner_for, gates )
        self._classifier = classifier

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        action = RecordedAction( action_type="click", locators=locators_from( args ),
                                 metadata={ "text": str( args.get( "text" ) or args.get( "label" ) or "" )})
        action.requires_approval = bool( self._classifier.reason( action ))
        return self.run_step( browser, action, args )


class ExecuteText( _AdHocStep ):
    """Types a value an agent supplied. A password field is refused: secrets
    are typed by a person, at playback, through the secret prompt."""
    name        = "execute_text"
    description = "Type text into one field (never a password field)"
    redact      = ( "value", )

    def __init__( self, attachment: IBrowserAttachment, runner_for: RunnerFor, gates: FlagGateChooser,
                  resolver: IElementResolver ) -> None:
        super().__init__( attachment, runner_for, gates )
        self._resolver = resolver

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        locators = locators_from( args )
        element, _ = self._resolver.resolve( browser, locators )
        if element is not None and ( element.get_attribute( "type" ) or "" ).lower() == "password":
            return refused( "that is a password field; a person types secrets, not this ADW" )
        action = RecordedAction( action_type="enter_text", locators=locators, value=str( args.get( "value" ) or "" ))
        return self.run_step( browser, action, args )


class WaitFor( _OnBrowser ):
    name        = "wait_for"
    description = "Wait for one explicit condition on the live page"

    def __init__( self, attachment: IBrowserAttachment, waiter: IConditionWaiter ) -> None:
        super().__init__( attachment )
        self._waiter = waiter

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        condition = WaitCondition( kind=args.get( "kind" ) or "navigation_complete", value=str( args.get( "value" ) or "" ),
                                   timeout_seconds=float( args.get( "timeout" ) or 15 ))
        problem = self._waiter.wait( browser, [ condition ])
        return refused( problem ) if problem else ok( f"{ condition.kind } { condition.value }".strip() + " holds" )


class Navigate( _OnBrowser ):
    name        = "navigate"
    description = "Open a URL in the debug Chrome's current tab and wait for it to load"

    def __init__( self, attachment: IBrowserAttachment, waiter: IConditionWaiter ) -> None:
        super().__init__( attachment )
        self._waiter = waiter

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        url = str( args.get( "url" ) or "" )
        if not url.startswith(( "http://", "https://" )):
            return refused( "a URL must start with http:// or https://" )
        browser.navigate( url )
        problem = self._waiter.wait( browser, [ WaitCondition( kind="navigation_complete" )])
        return refused( problem ) if problem else ok( f"opened { browser.current_url }", url=browser.current_url )


class ExecuteStep( _OnBrowser ):
    name        = "execute_step"
    description = "Run one step of a saved playback against the live page"

    def __init__( self, attachment: IBrowserAttachment, runner_for: RunnerFor, gates: FlagGateChooser,
                  repository: IPlaybackRepository ) -> None:
        super().__init__( attachment )
        self._runner_for = runner_for
        self._gates      = gates
        self._repository = repository

    def on( self, browser: IBrowserAdapter, args: dict[ str, Any ]) -> OperationOutcome:
        recording = self._repository.by_name( str( args.get( "name" ) or "" ))
        if recording is None:
            return refused( f"no playback called { args.get( 'name' )!r}" )
        wanted = int( args.get( "position" ) or 0 )
        step   = next(( s for s in recording.steps if s.position == wanted ), None )
        if step is None:
            return refused( f"'{ recording.name }' has no step { wanted }; it has { len( recording.steps )}" )
        return _outcome( self._runner_for( *self._gates.gates( args )).run( browser, step, recording, 0 ))

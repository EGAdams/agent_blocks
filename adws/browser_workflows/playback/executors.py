"""IActionExecutor and its Decorators.

`CommandActionExecutor` runs a step's command. `SecretFillingExecutor` hands
it a secret at the last moment, in a copy of the step that is never kept.
`RetryingExecutor` repeats an action only when the browser said it did not
happen (something covered the element), never after it may have.
"""
from __future__ import annotations

from browser_session.interfaces.system import IClock

from ..errors import StepBlocked
from ..interfaces.browser import IBrowserAdapter, IWorkflowElement
from ..interfaces.playback import IActionCommandFactory, IActionExecutor, ISecretSource
from ..models import RecordingStep

SECRET_ACTIONS     = ( "enter_text", "select_option" )
NOT_DONE_EXCEPTIONS = ( "ElementClickInterceptedException", "ElementNotInteractableException" )


class CommandActionExecutor:
    def __init__( self, factory: IActionCommandFactory ) -> None:
        self._factory = factory

    def execute( self, browser: IBrowserAdapter, step: RecordingStep, element: IWorkflowElement | None ) -> str:
        return self._factory.create( step ).execute( browser, element )


class SecretFillingExecutor:
    def __init__( self, inner: IActionExecutor, secrets: ISecretSource ) -> None:
        self._inner   = inner
        self._secrets = secrets

    def execute( self, browser: IBrowserAdapter, step: RecordingStep, element: IWorkflowElement | None ) -> str:
        if not ( step.value_is_secret and step.action_type in SECRET_ACTIONS ):
            return self._inner.execute( browser, step, element )
        secret = self._secrets.secret_for( step )
        if not secret:
            raise StepBlocked( "this step needs a secret value and none was supplied" )
        return self._inner.execute( browser, step.model_copy( update={ "value": secret }), element )


class RetryingExecutor:
    def __init__( self, inner: IActionExecutor, clock: IClock, attempts: int = 3, pause_seconds: float = 0.5 ) -> None:
        self._inner    = inner
        self._clock    = clock
        self._attempts = max( 1, attempts )
        self._pause    = pause_seconds

    def execute( self, browser: IBrowserAdapter, step: RecordingStep, element: IWorkflowElement | None ) -> str:
        for attempt in range( 1, self._attempts + 1 ):
            try:
                return self._inner.execute( browser, step, element )
            except Exception as exc:
                if type( exc ).__name__ not in NOT_DONE_EXCEPTIONS or attempt == self._attempts:
                    raise
                self._clock.sleep( self._pause )
        raise AssertionError( "unreachable" )

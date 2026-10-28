"""Command: one class per action type, built from its step by a factory.

A command does its one browser action and returns what it did, in words
that never include a secret value. Waiting, locating and gating are someone
else's job (`step_runner.py`).
"""
from __future__ import annotations

from ..errors import BrowserWorkflowError
from ..interfaces.browser import IBrowserAdapter, IWorkflowElement
from ..interfaces.playback import IActionCommand, IActionCommandBuilder, IActionCommandFactory
from ..models import RecordingStep


def _shown( step: RecordingStep ) -> str:
    return "(secret)" if step.value_is_secret else repr( step.value or "" )


class NavigateCommand:
    needs_element = False

    def __init__( self, step: RecordingStep ) -> None:
        self._url = step.url

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        browser.navigate( self._url )
        return f"opened { self._url }"


class ClickCommand:
    needs_element = True

    def __init__( self, step: RecordingStep ) -> None:
        self._step = step

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        element.click()
        return f"clicked { self._step.metadata.get( 'text' ) or element.tag_name }".strip()


class EnterTextCommand:
    needs_element = True

    def __init__( self, step: RecordingStep ) -> None:
        self._step = step

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        if self._step.value is None:
            raise BrowserWorkflowError( "no text to enter" )
        element.clear()
        element.send_keys( self._step.value )
        return f"entered { _shown( self._step )}"


class SelectOptionCommand:
    needs_element = True

    def __init__( self, step: RecordingStep ) -> None:
        self._step = step

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        browser.select_option( element, self._step.value or "" )
        return f"selected { _shown( self._step )}"


class SetCheckedCommand:
    """Idempotent: sets the box to the recorded state rather than toggling it."""
    needs_element = True

    def __init__( self, step: RecordingStep ) -> None:
        self._want = ( step.value or "true" ).lower() in ( "true", "1", "on", "yes", "checked" )

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        if element.is_selected() != self._want:
            element.click()
            return f"{ 'checked' if self._want else 'unchecked' } it"
        return f"already { 'checked' if self._want else 'unchecked' }"


class PressKeyCommand:
    """The key goes to the recorded element if it has locators, else the page."""

    def __init__( self, step: RecordingStep ) -> None:
        self._key         = step.value or "Enter"
        self.needs_element = bool( step.locators )

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        browser.press_key( element, self._key )
        return f"pressed { self._key }"


class WaitCommand:
    """Does nothing itself: a wait step is its `wait_after` conditions."""
    needs_element = False

    def __init__( self, step: RecordingStep ) -> None:
        pass

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        return "waited"


class SwitchTabCommand:
    """Focuses the first tab whose URL contains the step's url (or value)."""
    needs_element = False

    def __init__( self, step: RecordingStep ) -> None:
        self._want = step.url or step.value or ""

    def execute( self, browser: IBrowserAdapter, element: IWorkflowElement | None ) -> str:
        for tab in browser.tabs():
            if self._want and self._want in tab.url:
                browser.focus( tab.handle )
                return f"switched to { tab.url }"
        raise BrowserWorkflowError( f"no open tab matches { self._want!r}" )


class ActionCommandFactory:
    def __init__( self ) -> None:
        self._builders: dict[ str, IActionCommandBuilder ] = {}

    def register( self, action_type: str, builder: IActionCommandBuilder ) -> None:
        self._builders[ action_type ] = builder

    def create( self, step: RecordingStep ) -> IActionCommand:
        builder = self._builders.get( step.action_type )
        if builder is None:
            raise BrowserWorkflowError( f"no command for action type { step.action_type!r}" )
        return builder( step )


def default_command_factory() -> IActionCommandFactory:
    factory = ActionCommandFactory()
    for name, builder in { "navigate": NavigateCommand, "click": ClickCommand, "enter_text": EnterTextCommand,
                           "select_option": SelectOptionCommand, "set_checked": SetCheckedCommand,
                           "press_key": PressKeyCommand, "wait": WaitCommand, "switch_tab": SwitchTabCommand }.items():
        factory.register( name, builder )
    return factory

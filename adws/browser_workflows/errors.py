"""The failures browser_workflows names, so a caller can tell them apart."""
from __future__ import annotations


class BrowserWorkflowError( Exception ):
    """Base: a failure this package expected and can explain in one line."""


class IllegalTransition( BrowserWorkflowError ):
    """A command that makes no sense in the current state. Nothing changed."""


class DuplicateName( BrowserWorkflowError ):
    """A playback with that name already exists."""


class NotFound( BrowserWorkflowError ):
    """No such recording, step, run or repair."""


class ExtensionNotConnected( BrowserWorkflowError ):
    """The recorder extension is not connected to the service."""


class StepBlocked( BrowserWorkflowError ):
    """A step may not run without a person: approval refused, or a secret not supplied."""


class ElementNotFound( BrowserWorkflowError ):
    """No recorded locator found exactly one usable element in time."""

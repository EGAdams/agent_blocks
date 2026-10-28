"""From what the page saw to what playback does."""
from __future__ import annotations

from typing import Protocol

from ..models import RawBrowserEvent, RecordedAction


class IBrowserEventCapture( Protocol ):
    """Python's side of capture: accepts one raw event for the active session.
    (The page-side capture is the extension's content script.)"""

    def capture( self, session_id: str, event: RawBrowserEvent ) -> bool: ...


class ISensitivityPolicy( Protocol ):
    """Is this field a secret? Returns the reason, or "" when it is not."""

    def reason( self, input_type: str, hints: dict[ str, str ]) -> str: ...


class IEventScrubber( Protocol ):
    """Takes the secret out of a raw event before anything stores it."""

    def scrub( self, event: RawBrowserEvent ) -> RawBrowserEvent: ...


class IConsequenceClassifier( Protocol ):
    """Would doing this be hard to undo (pay, transfer, delete, submit)?"""

    def reason( self, action: RecordedAction ) -> str: ...


class INormalizationRule( Protocol ):
    """One rewrite of the action list. Rules run in order (a pipeline)."""

    @property
    def name( self ) -> str: ...

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]: ...


class IEventMapper( Protocol ):
    """One raw event to zero or one action."""

    def map( self, event: RawBrowserEvent ) -> RecordedAction | None: ...


class IActionNormalizer( Protocol ):
    def normalize( self, events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]: ...

"""IEventScrubber: the Python side's own check, before an event is staged."""
from __future__ import annotations

from ..interfaces.normalizing import ISensitivityPolicy
from ..models import RawBrowserEvent


class SensitiveValueScrubber:
    def __init__( self, policy: ISensitivityPolicy ) -> None:
        self._policy = policy

    def scrub( self, event: RawBrowserEvent ) -> RawBrowserEvent:
        if event.kind not in ( "text_committed", "select", "key" ):
            return event
        reason = event.sensitive_reason or self._policy.reason( event.input_type, event.field_hints )
        if not ( reason or event.sensitive ):
            return event
        update = { "sensitive": True, "sensitive_reason": reason or "marked sensitive by the page" }
        if event.kind != "key":         # Enter is not a secret; what was typed before it is.
            update[ "value" ] = None
        return event.model_copy( update=update )

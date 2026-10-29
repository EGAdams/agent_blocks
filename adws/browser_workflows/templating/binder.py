"""IRecordingBinder: a templated recording with its blanks filled in."""
from __future__ import annotations

from ..errors import BrowserWorkflowError
from ..models import Recording, RecordingStep
from .placeholders import fill, names_in


def _texts( step: RecordingStep ) -> list[ str | None ]:
    return [ step.url, None if step.value_is_secret else step.value, *( c.value for c in step.locators )]


class PlaceholderRecordingBinder:
    """Fills locator values, urls and non-secret values. A secret is never
    a template: it is asked for at playback, as always."""

    def placeholders( self, recording: Recording ) -> list[ str ]:
        found: list[ str ] = []
        for step in recording.steps:
            for text in _texts( step ):
                found += [ n for n in names_in( text ) if n not in found ]
        return found

    def bind( self, recording: Recording, values: dict[ str, str ]) -> Recording:
        missing = [ n for n in self.placeholders( recording ) if not values.get( n )]
        if missing:
            raise BrowserWorkflowError( f"'{ recording.name }' needs { ', '.join( missing )}; "
                                        "say --period \"October 2025\" or --params '{\"name\": \"value\"}'" )
        steps = []
        for step in recording.steps:
            steps.append( step.model_copy( update={
                "url":      fill( step.url, values ),
                "value":    step.value if step.value_is_secret else fill( step.value, values ),
                "locators": [ c.model_copy( update={ "value": fill( c.value, values )}) for c in step.locators ],
            }))
        return recording.model_copy( update={ "steps": steps })

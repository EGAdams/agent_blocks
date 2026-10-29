"""IPlaybackParameterizer: what would turn a recording of one month into a
template for any month. A chain of rules, each a Strategy; the result is a
proposal a person reads, never a save.

For Chase's "download the September 2025 statement":
  step 6  text "2025"                                     -> text "{year}"
  step 7  text "Sep 12, 2025 Opens menu to save document" -> text_pattern "{month_abbr} *, {year} Opens ..."
          and its css "#accountsTable-0-row3-..." is dropped: it is September's row.
"""
from __future__ import annotations

import re

from ..interfaces.templating import IParameterizeRule
from ..models import LocatorCandidate, Recording, RecordingStep, TemplateChange
from .placeholders import names_in

#: The example values a rule looks for, in this order. Numeric months ("9",
#: "09") and two-digit years are left out: on a page they are mostly something else.
TEMPLATED_NAMES = ( "month_name", "month_abbr", "year" )
WORDY           = ( "text", "label", "text_pattern" )
POSITIONAL      = re.compile( r"row\d+|\d+-\d+" )      # ids that name a table row by its index


def _templated( locators: list[ LocatorCandidate ]) -> bool:
    return any( names_in( c.value ) for c in locators )


class ExampleValueRule:
    """The recording's own month and year, wherever a person reads them, become placeholders."""

    def __init__( self, names: tuple[ str, ... ] = TEMPLATED_NAMES ) -> None:
        self._names = names

    def apply( self, step: RecordingStep, locators: list[ LocatorCandidate ],
               example: dict[ str, str ]) -> tuple[ list[ LocatorCandidate ], list[ str ]]:
        notes, out = [], []
        for c in locators:
            value = c.value
            if c.strategy in WORDY:
                for name in self._names:
                    seen = example.get( name )
                    if seen:
                        value = re.sub( rf"(?<![A-Za-z0-9]){ re.escape( seen )}(?![A-Za-z0-9])", "{" + name + "}", value )
            if value != c.value:
                notes.append( f"{ c.strategy } { c.value!r} -> { value!r}" )
            out.append( c.model_copy( update={ "value": value }))
        return out, notes


class DayWildcardRule:
    """A statement's day differs every month ("Sep 12", "Oct 10"), so the
    day after a templated month becomes `*` and the text a pattern."""

    DAY = re.compile( r"(\{month_(?:abbr|name)\})\s+\d{1,2}\b" )

    def apply( self, step: RecordingStep, locators: list[ LocatorCandidate ],
               example: dict[ str, str ]) -> tuple[ list[ LocatorCandidate ], list[ str ]]:
        notes, out = [], []
        for c in locators:
            if c.strategy in ( "text", "text_pattern" ) and self.DAY.search( c.value ):
                value = self.DAY.sub( r"\1 *", c.value )
                notes.append( f"the day in { c.value!r} matches any day: text_pattern { value!r}" )
                c = c.model_copy( update={ "strategy": "text_pattern", "value": value })
            out.append( c )
        return out, notes


class PinnedLocatorRule:
    """On a templated step, a locator with no placeholder still finds the
    example's element (September's row), so it goes. Elsewhere, an id that
    names a table row by index goes too, while another locator remains."""

    def apply( self, step: RecordingStep, locators: list[ LocatorCandidate ],
               example: dict[ str, str ]) -> tuple[ list[ LocatorCandidate ], list[ str ]]:
        if _templated( locators ):
            keep = [ c for c in locators if names_in( c.value )]
        else:
            keep = [ c for c in locators if not ( c.strategy == "css" and POSITIONAL.search( c.value ))] or locators
        gone = [ c for c in locators if c not in keep ]
        return keep, [ f"dropped { c.strategy } { c.value!r} (pinned to the recorded row)" for c in gone ]


def default_parameterize_rules() -> list[ IParameterizeRule ]:
    return [ ExampleValueRule(), DayWildcardRule(), PinnedLocatorRule()]


class RuleChainParameterizer:
    def __init__( self, rules: list[ IParameterizeRule ]) -> None:
        self._rules = rules

    def propose( self, recording: Recording, example: dict[ str, str ]) -> list[ TemplateChange ]:
        changes = []
        for step in recording.steps:
            locators, notes = list( step.locators ), []
            for rule in self._rules:
                locators, said = rule.apply( step, locators, example )
                notes += said
            if locators != step.locators:
                changes.append( TemplateChange( position=step.position, before=step.locators, after=locators, notes=notes ))
        return changes

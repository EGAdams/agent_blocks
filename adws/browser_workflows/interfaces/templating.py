"""Playbacks with blanks in them: one recording that fetches any month.

A templated playback's locator values hold `{placeholder}`s ("{month_abbr} *,
{year} Opens menu to save document"); playback fills them in from a period or
from explicit values, and refuses before touching the browser if one is left
empty. Parameterising is proposed by rules and saved only when a person says so.
"""
from __future__ import annotations

from typing import Any, Protocol

from ..models import LocatorCandidate, Period, Recording, RecordingStep, StatementSource, TemplateChange


class IRecordingBinder( Protocol ):
    """Fills a templated recording's placeholders; a copy, never a save."""

    def placeholders( self, recording: Recording ) -> list[ str ]: ...

    def bind( self, recording: Recording, values: dict[ str, str ]) -> Recording: ...


class IPeriodParser( Protocol ):
    """"October 2025", "Oct '25", "10/2025", "last month" -> a Period, or None."""

    def parse( self, text: str ) -> Period | None: ...


class IPeriodVocabulary( Protocol ):
    """A Period as placeholder values: year, yy, month, month_2, month_name, month_abbr."""

    def values( self, period: Period ) -> dict[ str, str ]: ...

    def said( self, period: Period ) -> str: ...


class IParameterSource( Protocol ):
    """An operation's arguments (--period, --params) as placeholder values."""

    def values( self, args: dict[ str, Any ]) -> dict[ str, str ]: ...


class IParameterizeRule( Protocol ):
    """Strategy: one way a recorded step's locators become a template's.
    `example` is the recording's own values, e.g. September 2025's."""

    def apply( self, step: RecordingStep, locators: list[ LocatorCandidate ],
               example: dict[ str, str ]) -> tuple[ list[ LocatorCandidate ], list[ str ]]: ...


class IPlaybackParameterizer( Protocol ):
    """The changes that would turn a recording into a template. Proposes only."""

    def propose( self, recording: Recording, example: dict[ str, str ]) -> list[ TemplateChange ]: ...


class IStatementCatalog( Protocol ):
    """Which bank a request names, and the playback that fetches its statements."""

    def find( self, text: str ) -> StatementSource | None: ...

    def sources( self ) -> list[ StatementSource ]: ...

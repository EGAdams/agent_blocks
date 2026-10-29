"""Months: reading one out of a request, and writing one as placeholder values."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date
from typing import Any, Callable

from ..errors import BrowserWorkflowError
from ..interfaces.templating import IPeriodParser, IPeriodVocabulary
from ..models import Period

_NAMES  = { name.casefold(): i for i, name in enumerate( calendar.month_name ) if name }
_NAMES |= { abbr.casefold(): i for i, abbr in enumerate( calendar.month_abbr ) if abbr }
_NAMES[ "sept" ] = 9
_MONTH  = r"\b(?P<m>" + "|".join( sorted( _NAMES, key=len, reverse=True )) + r")\b\.?"
_DAY    = r"(?:\s+\d{1,2}(?:st|nd|rd|th)?,?)?"

_WITH_YEAR    = re.compile( _MONTH + _DAY + r",?\s+(?P<y>\d{4})\b", re.I )
_WITH_SHORT   = re.compile( _MONTH + r",?\s+'?(?P<y>\d{2})\b(?!\s*,?\s*\d{4})", re.I )
_YEAR_FIRST   = re.compile( r"\b(?P<y>\d{4})[-/](?P<m>\d{1,2})\b" )
_MONTH_FIRST  = re.compile( r"\b(?P<m>\d{1,2})[-/](?P<y>\d{4})\b" )
_ALONE        = re.compile( _MONTH, re.I )
_RELATIVE     = re.compile( r"\b(?P<which>last|previous|this|current)\s+month\b", re.I )
_NOT_ALONE    = { "may", "march" }                  # verbs too often: these need a year


class WordPeriodParser:
    """IPeriodParser. A month named without a year is the latest one not in
    the future; "October 25" is October 2025, since a statement has no day."""

    def __init__( self, today: Callable[[], date ] = date.today ) -> None:
        self._today = today

    def parse( self, text: str ) -> Period | None:
        for pattern, short in (( _WITH_YEAR, False ), ( _WITH_SHORT, True )):
            m = pattern.search( text )
            if m:
                year = int( m.group( "y" ))
                return Period( year=2000 + year if short else year, month=_NAMES[ m.group( "m" ).casefold()])
        for pattern in ( _YEAR_FIRST, _MONTH_FIRST ):
            m = pattern.search( text )
            if m and 1 <= int( m.group( "m" )) <= 12:
                return Period( year=int( m.group( "y" )), month=int( m.group( "m" )))
        today = self._today()
        m = _RELATIVE.search( text )
        if m:
            back = 1 if m.group( "which" ).casefold() in ( "last", "previous" ) else 0
            index = today.year * 12 + today.month - 1 - back
            return Period( year=index // 12, month=index % 12 + 1 )
        for m in _ALONE.finditer( text ):
            word = m.group( "m" ).casefold()
            if word in _NOT_ALONE:
                continue
            month = _NAMES[ word ]
            return Period( year=today.year if month <= today.month else today.year - 1, month=month )
        return None


class CalendarPeriodVocabulary:
    """IPeriodVocabulary, in English month names, as the banks' pages write them."""

    def values( self, period: Period ) -> dict[ str, str ]:
        return { "year": str( period.year ), "yy": f"{ period.year % 100 :02d}", "month": str( period.month ),
                 "month_2": f"{ period.month :02d}", "month_name": calendar.month_name[ period.month ],
                 "month_abbr": calendar.month_abbr[ period.month ]}

    def said( self, period: Period ) -> str:
        return f"{ calendar.month_name[ period.month ]} { period.year }"


class PeriodParameterSource:
    """IParameterSource: --period "October 2025" spelled out, then --params on top."""

    def __init__( self, parser: IPeriodParser, vocabulary: IPeriodVocabulary ) -> None:
        self._parser     = parser
        self._vocabulary = vocabulary

    def values( self, args: dict[ str, Any ]) -> dict[ str, str ]:
        found: dict[ str, str ] = {}
        if args.get( "period" ):
            period = self._parser.parse( str( args[ "period" ]))
            if period is None:
                raise BrowserWorkflowError( f"no month in { args[ 'period' ]!r}; say e.g. \"October 2025\"" )
            found |= self._vocabulary.values( period )
        params = args.get( "params" ) or {}
        params = json.loads( params ) if isinstance( params, str ) else params
        if not isinstance( params, dict ):
            raise BrowserWorkflowError( "--params takes a JSON object, e.g. '{\"year\": \"2025\"}'" )
        return found | { str( k ): str( v ) for k, v in params.items()}

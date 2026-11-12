"""CalendarStatementLocator: IStatementLocator -- the ways a statement list
writes one month, best first, from the shared calendar vocabulary (the same
one templated playbacks are filled from)."""
from __future__ import annotations

from browser_workflows.interfaces.templating import IPeriodVocabulary
from browser_workflows.models import Period


class CalendarStatementLocator:
    def __init__( self, vocabulary: IPeriodVocabulary ) -> None:
        self._vocabulary = vocabulary

    def words( self, period: Period ) -> list[ str ]:
        v = self._vocabulary.values( period )
        return [ f"{ v[ 'month_name' ]} { v[ 'year' ]}", f"{ v[ 'month_name' ]}, { v[ 'year' ]}",
                 f"{ v[ 'month_abbr' ]} { v[ 'year' ]}", f"{ v[ 'month_2' ]}/{ v[ 'year' ]}",
                 f"{ v[ 'year' ]}-{ v[ 'month_2' ]}", f"{ v[ 'month_2' ]}/{ v[ 'yy' ]}",
                 # a list that names each statement by its closing date (FNBO: "Statement Closing 01/02/2026")
                 *( f"{ v[ 'month_2' ]}/{ day:02d}/{ v[ 'year' ]}" for day in range( 1, 32 ))]

"""TextTransferRecords: ITransferRecords over rol_finances' readable export
(transfer_pay_activity.txt: "To:", "From:", "Date:", "Amount:" blocks).

It only suggests: a payee is offered with how often it was paid, from which
(masked) account and when last, and the person says whether that is the one.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from ..models import TransferHint
from ..page.ranker import tokens

_FIELD = re.compile( r"^(Date|To|From|Amount):\s*(.*)$" )
_LAST4 = re.compile( r"ending in (\d{4})" )
_NOISE = frozenset({ "bill", "bills", "payment", "transfer", "transfers", "activity" })


class TextTransferRecords:
    def __init__( self, path: Path ) -> None:
        self._path = Path( path )

    def candidates( self, subject: str, limit: int = 3 ) -> list[ TransferHint ]:
        wanted = tokens( subject ) - _NOISE
        if not wanted or not self._path.is_file():
            return []
        by_payee: dict[ str, list[ dict[ str, str ]]] = defaultdict( list )
        for record in self._records():
            if record.get( "To" ):
                by_payee[ record[ "To" ]].append( record )
        hints = []
        for payee, records in by_payee.items():
            shared = wanted & tokens( payee )
            if not shared:
                continue
            accounts = Counter( f"•{ m.group( 1 ) }" for r in records for m in [ _LAST4.search( r.get( "From", "" ))] if m )
            hints.append( TransferHint( payee=payee, count=len( records ), accounts=[ a for a, _ in accounts.most_common()],
                                        last_date=records[ 0 ].get( "Date", "" ), last_amount=records[ 0 ].get( "Amount", "" ),
                                        score=round( len( shared ) / len( wanted ), 3 )))
        hints.sort( key=lambda h: ( -h.score, -h.count ))
        return hints[ :limit ]

    def _records( self ) -> list[ dict[ str, str ]]:
        records, current = [], {}
        for line in self._path.read_text( encoding="utf-8", errors="replace" ).splitlines():
            if line.startswith( "Transfer:" ):
                if current:
                    records.append( current )
                current = {}
            match = _FIELD.match( line.strip())
            if match:
                current[ match.group( 1 )] = match.group( 2 ).strip()
        if current:
            records.append( current )
        return records

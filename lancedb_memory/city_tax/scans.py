"""Asking for a paper document, and noticing when it has been scanned.

No second scanning system. `ScanDesk` (IDocumentScanRequester) puts the
request on the company to-do list -- the list EG already hears through Toyota
and Liz -- and watches Scala's ledger, where the scanner pipeline records
every page it sees (finance scans included: Scala logs Mazda's as "left to
Mazda"). The first scan logged after the request is the candidate. A path
EG names ("I put it at C:/Users/.../payment.pdf") is the manual fallback.

A scan is only ever *offered*: Liz reads it (Scala's tesseract reader), lists
the dollar amounts she sees, and waits for EG to say which one is the
answer. Nothing read off a scan goes on the return unconfirmed.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .models import ScanRequest, ScanState

_DOLLARS = re.compile( r"\$?\s?(\d{1,3}(?:,\d{3})*\.\d\d)\b" )


class ITodoAdd( Protocol ):
    def add( self, text: str, who: str ): ...
    def complete( self, number: int, who: str ): ...


class ILedgerRecent( Protocol ):
    def recent( self, limit: int ) -> list: ...


class IOcrRead( Protocol ):
    def read( self, image: Path ) -> str: ...


def dollar_amounts( text: str ) -> list[ str ]:
    seen: list[ str ] = []
    for amount in _DOLLARS.findall( text ):
        if amount not in seen:
            seen.append( amount )
    return seen[ :12 ]


class ScanDesk:
    def __init__( self, todos: ITodoAdd, ledger: ILedgerRecent, ocr: IOcrRead, who: str = "liz" ) -> None:
        self._todos = todos
        self._ledger = ledger
        self._ocr = ocr
        self._who = who

    def request( self, scan: ScanRequest ) -> ScanRequest:
        if scan.todo_number is not None:
            return scan                                     # already asked: don't ask twice
        item = self._todos.add( f"Liz needs a scan: { scan.document }. Why: { scan.reason } Put it in the "
                                f"scanner, then tell Liz \"I've finished scanning it\" -- or \"Liz, there "
                                f"isn't one\" if it doesn't exist.", self._who )
        return scan.model_copy( update={ "todo_number": item.number })

    def _read( self, scan: ScanRequest, path: Path ) -> ScanRequest:
        text = self._ocr.read( path ) if path.is_file() else ""
        if not text.strip():
            return scan.model_copy( update={ "received": str( path ), "text_excerpt": "",
                                             "candidates": [], "state": ScanState.WAITING })
        excerpt = re.sub( r"\s+", " ", text ).strip()[ :240 ]
        return scan.model_copy( update={ "received": str( path ), "text_excerpt": excerpt,
                                         "candidates": dollar_amounts( text ), "state": ScanState.RECEIVED })

    def check( self, scan: ScanRequest, path: Path | None = None ) -> ScanRequest:
        """Look for the scan (or read the named file). Never closes the request by itself."""
        if scan.state is not ScanState.WAITING:
            return scan
        if path is not None:
            return self._read( scan, path )
        for outcome in self._ledger.recent( 50 ):
            at = outcome.at if isinstance( outcome.at, datetime ) else None
            if at is not None and at > scan.requested_at:
                return self._read( scan, Path( outcome.source ))
        return scan

    def close( self, scan: ScanRequest, state: ScanState, answer: str = "" ) -> ScanRequest:
        if scan.todo_number is not None:
            try:
                self._todos.complete( scan.todo_number, self._who )
            except KeyError:
                pass
        return scan.model_copy( update={ "state": state, "answer": answer,
                                         "closed_at": datetime.now().astimezone()})

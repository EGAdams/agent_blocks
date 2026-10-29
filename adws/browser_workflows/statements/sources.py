"""Which bank, which playback. Pure data -- `NameStatementCatalog` serves it.

A new bank is one entry here plus its recording: record one month's
download, then `adw_browser_parameterize_playback.py` turns it into the
named playback. Nothing else changes.
"""
from __future__ import annotations

from ..models import StatementSource

#: Recorded as "download_sept_2025_statement" (Accounts -> menu -> Statements
#: & documents -> year -> the month's row -> Save as PDF), then parameterised.
CHASE = StatementSource(
    key="chase",
    title="Chase",
    names=[ "chase", "jpmorgan", "jp morgan" ],
    playback="chase_statement_by_month",
)

ALL_SOURCES: list[ StatementSource ] = [ CHASE ]

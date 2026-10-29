from __future__ import annotations

from typing import Protocol

from ..models import CashyEvent


class ICashyObserver( Protocol ):
    """Observer: sees every activity, so a run is watchable and loggable."""

    def notify( self, session_id: str, event: CashyEvent ) -> None: ...

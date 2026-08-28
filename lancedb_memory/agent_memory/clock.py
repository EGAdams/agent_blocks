"""Clock implementations. Exists so chronological recall order is testable."""
from datetime import datetime, timezone


class SystemClock:
    def now( self ) -> datetime:
        return datetime.now( timezone.utc )

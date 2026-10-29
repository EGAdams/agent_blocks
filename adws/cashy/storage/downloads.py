"""SqliteDownloadLedger: IDownloadLedger -- what was fetched, so a month is not fetched twice."""
from __future__ import annotations

from browser_workflows.storage.database import SqliteDatabase

from ..models import DownloadRecord


class SqliteDownloadLedger:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    def find( self, bank: str, account: str, period: str ) -> DownloadRecord | None:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT * FROM cashy_downloads WHERE bank = ? AND account = ? AND period = ? "
                                "ORDER BY id DESC LIMIT 1", ( bank, account, period )).fetchone()
        return _record( row ) if row else None

    def add( self, record: DownloadRecord ) -> None:
        with self._db.connect() as conn:
            conn.execute( "INSERT INTO cashy_downloads ( bank, account, period, path, size, sha256, at ) "
                          "VALUES ( ?, ?, ?, ?, ?, ?, ? )", ( record.bank, record.account, record.period, record.path,
                                                               record.size, record.sha256, record.at ))

    def recent( self, limit: int = 20 ) -> list[ DownloadRecord ]:
        with self._db.connect() as conn:
            rows = conn.execute( "SELECT * FROM cashy_downloads ORDER BY id DESC LIMIT ?", ( limit, )).fetchall()
        return [ _record( r ) for r in rows ]


def _record( row ) -> DownloadRecord:
    data = dict( row )
    data.pop( "id", None )
    return DownloadRecord.model_validate( data )

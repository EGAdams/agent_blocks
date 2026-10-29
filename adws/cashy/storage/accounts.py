"""SqliteAccountBanks: IAccountBanks -- "•6285 is at Fifth Third", once told."""
from __future__ import annotations

from browser_workflows.storage.database import SqliteDatabase


class SqliteAccountBanks:
    def __init__( self, database: SqliteDatabase ) -> None:
        self._db = database

    def bank_for( self, account: str ) -> str:
        with self._db.connect() as conn:
            row = conn.execute( "SELECT bank FROM cashy_account_banks WHERE account = ?", ( account, )).fetchone()
        return row[ "bank" ] if row else ""

    def remember( self, account: str, bank: str ) -> None:
        with self._db.connect() as conn:
            conn.execute( "INSERT INTO cashy_account_banks ( account, bank ) VALUES ( ?, ? ) "
                          "ON CONFLICT ( account ) DO UPDATE SET bank = excluded.bank", ( account, bank ))

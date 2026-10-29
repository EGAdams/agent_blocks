from __future__ import annotations

from typing import Protocol

from ..models import TransferHint


class ITransferRecords( Protocol ):
    """The person's own transfer/pay records: which payee a request may mean."""

    def candidates( self, subject: str, limit: int = 3 ) -> list[ TransferHint ]: ...


class IAccountBanks( Protocol ):
    """Which bank holds a masked account, as the person has told Cashy."""

    def bank_for( self, account: str ) -> str: ...

    def remember( self, account: str, bank: str ) -> None: ...

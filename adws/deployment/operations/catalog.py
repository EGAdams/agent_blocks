"""Operation name -> operation. A new operation is one class and one line here."""
from __future__ import annotations

from .changes import Deploy, Restart, Rollback
from .reads import Health, History, Logs, Projects, Status

OPERATION_TABLE = {op.name: op for op in (Projects(), Status(), Health(), Logs(), History(),
                                          Deploy(), Restart(), Rollback())}

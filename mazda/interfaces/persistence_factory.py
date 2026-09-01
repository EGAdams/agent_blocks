"""Public contract for IPersistenceFactory. Known state: real SQLite implementation."""

from typing import Protocol

from mazda.models.factory_handles import UnitOfWorkHandle


class IPersistenceFactory( Protocol ):
    """Builds the schema, unit of work, and repositories backing storage."""

    def unit_of_work( self ) -> UnitOfWorkHandle:
        """Return a new unit of work bound to the current schema."""
        ...

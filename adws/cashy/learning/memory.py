"""LanceDBProcedureMemory: IProcedureMemory -- what Cashy learned, as vectors.

Each row is one sentence ("statement download at chase: Statements →
September 2025") and the pointer to the registered version that does it.
Nothing from a page goes in. The embedder is the lexical hashing one the
receptionists use, so it works offline. If lancedb is not installed the
memory says so (`available` is False) and Cashy carries on without it:
the SQLite registry, not this, is what playback runs from.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..models import ProcedureMemory

TABLE = "cashy_procedures"


class LanceDBProcedureMemory:
    def __init__( self, directory: Path, dimensions: int = 256 ) -> None:
        self._directory = Path( directory )
        self._dimensions = dimensions
        self._db = None
        self._embedder = None
        try:
            import lancedb                                          # noqa: F401  (optional dependency)
            from agent_memory.adapters.hashing_embedder import HashingEmbedder
        except ImportError:
            return
        self._directory.mkdir( parents=True, exist_ok=True )
        self._db = lancedb.connect( str( self._directory ))
        self._embedder = HashingEmbedder( dimensions )

    @property
    def available( self ) -> bool:
        return self._db is not None

    def remember( self, memory: ProcedureMemory ) -> None:
        if not self.available:
            return
        row = { **memory.model_dump(), "vector": self._embed( memory.summary )}
        if TABLE in self._tables():
            self._db.open_table( TABLE ).add([ row ])
        else:
            self._db.create_table( TABLE, data=[ row ])

    def search( self, text: str, bank: str = "", limit: int = 5 ) -> list[ ProcedureMemory ]:
        if not self.available or TABLE not in self._tables():
            return []
        query = self._db.open_table( TABLE ).search( self._embed( text )).limit( limit * 4 )
        rows  = query.to_list()
        found = [ ProcedureMemory.model_validate({ k: r[ k ] for k in ProcedureMemory.model_fields })
                  for r in rows if not bank or r[ "bank" ] == bank ]
        return found[ :limit ]

    def _embed( self, text: str ) -> list[ float ]:
        return asyncio.run( self._embedder.embed( text ))

    def _tables( self ) -> list[ str ]:
        listed = self._db.table_names() if hasattr( self._db, "table_names" ) else self._db.list_tables()
        return list( getattr( listed, "tables", listed ))

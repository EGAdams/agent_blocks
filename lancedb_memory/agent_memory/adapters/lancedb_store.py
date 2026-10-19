"""The only module in the package that imports lancedb or pyarrow."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import lancedb
import pyarrow as pa

from ..models import EmbeddedTurn, MemoryQuery, RecalledTurn, Turn


def _escape( value: str ) -> str:
    return value.replace( "'", "''" )


class LanceDBMemoryStore:
    """Repository over a LanceDB table. Sole source of truth for recall.

    The table carries the message body, so retrieval never touches the
    filesystem -- unlike the original script, where a missing nexus file
    silently emptied the recalled conversation.
    """

    def __init__( self, uri: Path, table_name: str, dimensions: int ) -> None:
        self._dimensions = dimensions
        self._db = lancedb.connect( str( uri ))
        self._schema = pa.schema([
            pa.field( "id", pa.string()),
            pa.field( "vector", pa.list_( pa.float32(), dimensions )),
            pa.field( "session_id", pa.string()),
            pa.field( "speaker", pa.string()),
            pa.field( "time", pa.float64()),
            pa.field( "message", pa.string()),
            pa.field( "timestring", pa.string()),
        ])

        if table_name in self._db.table_names():
            self._table = self._db.open_table( table_name )
            self._assert_dimensions_match( table_name )
        else:
            self._table = self._db.create_table( table_name, schema=self._schema )

    def _assert_dimensions_match( self, table_name: str ) -> None:
        """Fail loud on an embedder swap rather than writing unsearchable rows."""
        field = self._table.schema.field( "vector" )
        existing = getattr( field.type, "list_size", None )
        if existing is not None and existing != self._dimensions:
            raise ValueError(
                f"Table '{ table_name }' stores { existing }-dim vectors but the "
                f"configured embedder produces { self._dimensions }. Use a new "
                f"table name, or re-embed the existing rows."
            )

    def add( self, turns: list[ EmbeddedTurn ]) -> None:
        rows = [ self._to_row( embedded ) for embedded in turns ]
        self._table.add( rows )

    def search( self, query: MemoryQuery ) -> list[ RecalledTurn ]:
        builder = self._table.search( query.vector, vector_column_name="vector" )
        if query.session_id is not None:
            builder = builder.where( f"session_id = '{ _escape( query.session_id )}'", prefilter=True )

        hits = builder.limit( query.limit ).to_list()
        return [ self._to_recalled( hit ) for hit in hits ]

    def recent( self, session_id: str, limit: int ) -> list[ Turn ]:
        """A plain filtered scan, no vector -- `.search()` with no query is a full scan."""
        rows = (
            self._table.search()
            .where( f"session_id = '{ _escape( session_id )}'" )
            .limit( max( limit, 1 ) * 50 )   # generous cap; sorted and trimmed below
            .to_list()
        )
        rows.sort( key=lambda row: row[ "time" ], reverse=True )
        return [ self._to_recalled( row ).turn for row in rows[ :limit ]]

    def _to_row( self, embedded: EmbeddedTurn ) -> dict[ str, Any ]:
        turn = embedded.turn
        return {
            "id": str( turn.id ),
            "vector": embedded.vector,
            "session_id": turn.session_id,
            "speaker": turn.speaker,
            "time": turn.created_at.timestamp(),
            "message": turn.message,
            "timestring": turn.created_at.strftime( "%A, %B %d, %Y at %I:%M%p %Z" ),
        }

    def _to_recalled( self, hit: dict[ str, Any ]) -> RecalledTurn:
        turn = Turn(
            id=UUID( hit[ "id" ]),
            session_id=hit[ "session_id" ],
            speaker=hit[ "speaker" ],
            message=hit[ "message" ],
            created_at=datetime.fromtimestamp( hit[ "time" ], tz=timezone.utc ),
        )
        return RecalledTurn( turn=turn, distance=float( hit.get( "_distance", 0.0 )))

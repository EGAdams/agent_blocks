"""The cluster ledger: one JSON file, written atomically (tmp + rename)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Cluster


class JsonClusterLedger:
    def __init__( self, path: Path ) -> None:
        self._path = path

    @property
    def path( self ) -> Path:
        return self._path

    def load( self ) -> dict[ str, Cluster ]:
        if not self._path.exists():
            return {}
        data = json.loads( self._path.read_text() or "{}" )
        return { k: Cluster.model_validate( v ) for k, v in data.get( "clusters", {}).items()}

    def save( self, clusters: dict[ str, Cluster ]) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        body = { "clusters": { k: c.model_dump( mode="json" ) for k, c in sorted( clusters.items())}}
        tmp = self._path.with_suffix( ".tmp" )
        tmp.write_text( json.dumps( body, indent=1 ))
        os.replace( tmp, self._path )

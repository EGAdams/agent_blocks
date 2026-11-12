"""The durable side of storage: the audit trail (JSONL), one run record per ADW run (rewritten at each phase),
and staged copies of originals kept while a destination is unreachable. All under one data directory on the
Bridge machine (default `adws/adw_data/scala_storage/`, gitignored)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AuditRecord

SAFE = re.compile( r"^[A-Za-z0-9_]{1,40}$" )


def _atomic_write( path: Path, text: str ) -> None:
    path.parent.mkdir( parents=True, exist_ok=True )
    part = path.with_name( path.name + ".part" )
    part.write_text( text, encoding="utf-8" )
    os.replace( part, path )


class JsonlAudit:
    def __init__( self, path: Path ) -> None:
        self._path = path

    def record( self, entry: AuditRecord ) -> None:
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with self._path.open( "a", encoding="utf-8" ) as handle:
            handle.write( entry.model_dump_json() + "\n" )

    def _all( self ) -> list[ AuditRecord ]:
        if not self._path.exists():
            return []
        out = []
        for line in self._path.read_text( encoding="utf-8" ).splitlines():
            try:
                out.append( AuditRecord.model_validate_json( line ))
            except ValueError:
                continue
        return out

    def for_scan( self, scan_id: str ) -> list[ AuditRecord ]:
        return [ r for r in self._all() if r.scan_id == scan_id ]

    def recent( self, limit: int ) -> list[ AuditRecord ]:
        return self._all()[ -limit:]


class JsonRunRecord:
    def __init__( self, path: Path, **header: Any ) -> None:
        self._path = path
        self._body: dict[ str, Any ] = { **header, "phases": []}

    def phase( self, phase_name: str, /, **facts: Any ) -> None:
        self._body[ "phases" ].append({ "at": datetime.now( timezone.utc ).isoformat(), **facts, "phase": phase_name })
        self._body[ "state" ] = phase_name
        _atomic_write( self._path, json.dumps( self._body, indent=1, default=str ))


class StagedPending:
    """IPendingCopies under <data>/pending/: `<scan_id>__<machine>.<ext>` plus a small JSON note."""

    def __init__( self, root: Path ) -> None:
        self._root = root

    def keep( self, scan_id: str, machine: str, folder: str, meta: dict, data: bytes ) -> str:
        if not ( SAFE.match( scan_id ) and SAFE.match( machine )):
            raise ValueError( "unsafe pending key" )
        self._root.mkdir( parents=True, exist_ok=True )
        path = self._root / f"{ scan_id }__{ machine }.bin"
        if not path.exists():
            part = path.with_name( path.name + ".part" )
            part.write_bytes( data )
            os.replace( part, path )
        _atomic_write( path.with_suffix( ".json" ), json.dumps({ "scan_id": scan_id, "machine": machine, "folder": folder,
                                                                "file": path.name, "meta": meta,
                                                                "kept_at": datetime.now( timezone.utc ).isoformat()}))
        return str( path )

    def drop( self, scan_id: str, machine: str ) -> None:
        for path in self._root.glob( f"{ scan_id }__{ machine }.*" ):
            path.unlink( missing_ok=True )

    def read( self, scan_id: str ) -> tuple[ bytes, dict ] | None:
        for note in sorted( self._root.glob( f"{ scan_id }__*.json" )):
            try:
                meta = json.loads( note.read_text( encoding="utf-8" ))[ "meta" ]
                return note.with_suffix( ".bin" ).read_bytes(), meta
            except ( OSError, ValueError, KeyError ):
                continue
        return None

    def listed( self ) -> list[ dict ]:
        out = []
        for note in sorted( self._root.glob( "*.json" )):
            try:
                out.append( json.loads( note.read_text( encoding="utf-8" )))
            except ( OSError, ValueError ):
                continue
        return out

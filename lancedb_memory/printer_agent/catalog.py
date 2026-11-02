"""OutboxCatalog: the approved print folder, as ids. Callers never name a path.

Only regular files directly inside the outbox count -- no subdirectories, no
symlinks, no hidden files -- and only `.pdf` and `.txt` within the size
limit. A document's id is a prefix of its content hash, so it changes when the
file does and a stale id is refused rather than printing something else. Reads
open with O_NOFOLLOW and re-check the hash on the bytes actually read.
"""
from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .errors import RequestRefused
from .models import CatalogDocument, DOCUMENT_ID

MIMES = { ".pdf": "application/pdf", ".txt": "text/plain" }


class CatalogError( RequestRefused ):
    """Safe to show: names a document id or a rule, never a path."""


class OutboxCatalog:
    def __init__( self, root: Path, max_bytes: int, page_count: Callable[[ Path ], int ] | None = None ) -> None:
        self._root       = root
        self._max_bytes  = max_bytes
        self._page_count = page_count

    def list_documents( self ) -> list[ CatalogDocument ]:
        if not self._root.is_dir():
            return []
        found: list[ CatalogDocument ] = []
        for entry in sorted( os.scandir( self._root ), key=lambda e: e.name ):
            document = self._describe( entry )
            if document is not None:
                found.append( document )
        return found

    def read( self, document_id: str ) -> tuple[ CatalogDocument, bytes ]:
        if not DOCUMENT_ID.fullmatch( document_id ):
            raise CatalogError( "not a document id" )
        for entry in sorted( os.scandir( self._root ), key=lambda e: e.name ) if self._root.is_dir() else []:
            document = self._describe( entry, count_pages=False )
            if document is None or document.document_id != document_id:
                continue
            data = self._read_exact( Path( entry.path ))
            if hashlib.sha256( data ).hexdigest() != document.sha256:
                raise CatalogError( f"{ document_id } changed while it was being read; list the documents again" )
            return document, data
        raise CatalogError( f"no document { document_id } in the print outbox (it may have been changed or removed)" )

    # -- helpers ----------------------------------------------------------------

    def _describe( self, entry: os.DirEntry, count_pages: bool = True ) -> CatalogDocument | None:
        if entry.name.startswith( "." ) or entry.is_symlink() or not entry.is_file( follow_symlinks=False ):
            return None
        mime = MIMES.get( Path( entry.name ).suffix.lower())
        info = entry.stat( follow_symlinks=False )
        if mime is None or not stat.S_ISREG( info.st_mode ) or info.st_size == 0 or info.st_size > self._max_bytes:
            return None
        try:
            sha = hashlib.sha256( self._read_exact( Path( entry.path ))).hexdigest()
        except OSError:
            return None
        pages = None
        if count_pages and mime == "application/pdf" and self._page_count is not None:
            try:
                pages = self._page_count( Path( entry.path ))
            except Exception:  # noqa: BLE001 -- an unreadable PDF is listed without a page count
                pages = None
        return CatalogDocument( document_id=f"doc_{ sha[ :16 ]}", name=entry.name[ :120 ], mime=mime,
                                bytes=info.st_size, sha256=sha, pages=pages,
                                modified_at=datetime.fromtimestamp( info.st_mtime ).astimezone())

    def _read_exact( self, path: Path ) -> bytes:
        fd = os.open( path, os.O_RDONLY | getattr( os, "O_NOFOLLOW", 0 ))
        try:
            if not stat.S_ISREG( os.fstat( fd ).st_mode ):
                raise CatalogError( "not a regular file" )
            chunks: list[ bytes ] = []
            total = 0
            while True:
                chunk = os.read( fd, 1 << 20 )
                if not chunk:
                    break
                total += len( chunk )
                if total > self._max_bytes:
                    raise CatalogError( "the document is larger than the size limit" )
                chunks.append( chunk )
            return b"".join( chunks )
        finally:
            os.close( fd )

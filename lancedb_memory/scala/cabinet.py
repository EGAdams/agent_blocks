"""IFilingCabinet over `C:\\Users\\NewUser\\scanned_documents` on this box.

Scala grows the tree as documents need it: the first water bill creates
`bills_511/water/`. Growth is fenced, because a folder name can come from
Haiku:

* only under `bills_511/` and `documents_511/`, one level deep;
* lower-case snake_case names, 2-40 characters -- nothing like `..`;
* never `rol_receipts/`: receipts are Mazda's paperwork, not Scala's.

Scans are *copied*, never moved. The source is Mazda's staged copy in
`incoming_scans/`, and her turn may still be reading it. Each filed copy is
named `<scan date>_<kind>_<first 12 of sha256>.<ext>`, so the same image
filed twice lands on the same name instead of beside itself.
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

TOPS = ( "bills_511", "documents_511" )
INBOX = "inbox"
_FOLDER = re.compile( rf"^({ '|'.join( TOPS ) })(/[a-z0-9][a-z0-9_]{{1,39}})?$" )


def default_root() -> Path:
    return Path( os.environ.get( "SCALA_CABINET", "/mnt/c/Users/NewUser/scanned_documents" ))


class FolderNotAllowed( ValueError ):
    pass


class FilingCabinet:
    def __init__( self, root: Path | None = None ) -> None:
        self._root = root or default_root()

    @property
    def root( self ) -> Path:
        return self._root

    def folders( self ) -> tuple[ str, ... ]:
        found: list[ str ] = []
        for top in TOPS:
            base = self._root / top
            if not base.is_dir():
                continue
            found.append( top )
            found.extend( f"{ top }/{ child.name }" for child in sorted( base.iterdir())
                          if child.is_dir() and _FOLDER.match( f"{ top }/{ child.name }" ))
        return tuple( found )

    def is_allowed( self, folder: str ) -> bool:
        return bool( _FOLDER.match( folder ))

    def file( self, image: Path, folder: str, sha256: str ) -> tuple[ Path, bool ]:
        if not self.is_allowed( folder ):
            raise FolderNotAllowed( folder )
        target = self._root / folder
        created = not target.is_dir()
        target.mkdir( parents=True, exist_ok=True )
        return self._copy( image, target, folder.rsplit( "/", 1 )[ -1 ], sha256 ), created

    def inbox( self, image: Path, sha256: str ) -> Path:
        target = self._root / INBOX
        target.mkdir( parents=True, exist_ok=True )
        return self._copy( image, target, "unsorted", sha256 )

    def _copy( self, image: Path, target: Path, kind: str, sha256: str ) -> Path:
        scanned = datetime.fromtimestamp( image.stat().st_mtime ).strftime( "%Y-%m-%d" )
        destination = target / f"{ scanned }_{ kind }_{ sha256[ :12 ] }{ image.suffix.lower() }"
        if not destination.exists():
            partial = destination.with_name( destination.name + ".part" )
            shutil.copy2( image, partial )
            os.replace( partial, destination )
        return destination

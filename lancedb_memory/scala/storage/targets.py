"""ITargetRegistry over storage_targets.json, and the one function that turns a person's folder words into an
approved folder -- or refuses.

A folder may be named three ways: `insurance/home`, `insurance/home/`, or the full path under that machine's
root (`/home/adamsl/file_cabinet/insurance/home/`). A bare leaf (`electric`) is accepted when exactly one
approved folder ends in it. Anything else -- `..`, `~`, a backslash, an absolute path outside the root, a folder
not on the list -- is refused before any machine is contacted.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .interfaces import StorageRefused
from .models import StorageTarget

PART = re.compile( r"^[a-z0-9][a-z0-9_]{0,39}$" )


def default_config() -> Path:
    return Path( os.environ.get( "SCALA_STORAGE_TARGETS", Path( __file__ ).with_name( "storage_targets.json" )))


class JsonTargetRegistry:
    def __init__( self, path: Path | None = None ) -> None:
        raw = json.loads(( path or default_config()).read_text( encoding="utf-8" ))
        self._targets = tuple( StorageTarget( machine=name, **spec ) for name, spec in raw[ "targets" ].items())
        for target in self._targets:
            bad = [ f for f in target.folders if not all( PART.match( p ) for p in f.split( "/" ))]
            if bad or os.path.normpath( target.root ) != target.root or not target.root.startswith( "/" ):
                raise ValueError( f"storage target { target.machine } is misconfigured: { bad or target.root }" )

    def targets( self ) -> tuple[ StorageTarget, ... ]:
        return self._targets

    def resolve( self, words: str ) -> StorageTarget:
        key = re.sub( r"['’]s$", "", words.strip().lower())
        for target in self._targets:
            if key == target.machine.lower() or key == target.hostname.lower() or key in target.aliases:
                return target
        raise StorageRefused( "unknown_machine", f"{ words!r} is not a storage machine; the machines are "
                              + ", ".join( f"{ t.machine } ({ t.hostname })" for t in self._targets ))


def resolve_folder( target: StorageTarget, words: str ) -> str:
    text = words.strip()
    if not text:
        raise StorageRefused( "folder_not_allowed", f"name a folder on { target.machine }: " + ", ".join( target.folders ))
    if "\\" in text or "~" in text or "\x00" in text or any( p == ".." for p in re.split( r"/+", text )):
        raise StorageRefused( "path_traversal", f"{ words!r} is refused: folders are named, not navigated" )
    if text.startswith( "/" ):
        prefix = target.root.rstrip( "/" ) + "/"
        if not text.startswith( prefix ):
            raise StorageRefused( "absolute_path_refused", f"{ words!r} is outside { target.machine }'s cabinet { target.root }" )
        text = text[ len( prefix ):]
    folder = text.strip( "/" )
    if folder in target.folders:
        return folder
    if "/" not in folder:
        leaves = [ f for f in target.folders if f.rsplit( "/", 1 )[ -1 ] == folder ]
        if len( leaves ) == 1:
            return leaves[ 0 ]
    raise StorageRefused( "folder_not_allowed", f"{ words!r} is not an approved folder on { target.machine }; approved: "
                          + ", ".join( target.folders ))

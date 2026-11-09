"""The roles. A new file type is one IRenderer registered in the composition root, never an edit here."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .models import Inspection

if TYPE_CHECKING:
    from .archive import Unpacked


class IRenderer( Protocol ):
    """Turns one kind of file into page images and/or text, writing images under `out_dir` only."""
    def handles( self, mime: str ) -> bool: ...
    def render( self, source: Path, mime: str, out_dir: Path ) -> Inspection: ...


class IFileInspector( Protocol ):
    """The whole job for one file: sniff, choose a renderer, write beside nothing but its own folder."""
    def inspect( self, source: Path, sha256: str ) -> Inspection: ...


class IArchiveUnpacker( Protocol ):
    """Writes an archive's readable files under `out_dir` and says why each other entry was left in."""
    def unpack( self, source: Path, out_dir: Path ) -> "Unpacked": ...

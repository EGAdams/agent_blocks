"""FileInspector: sniff, choose the first renderer that handles the type, write into one folder per file.

The folder is named by the file's sha256, so inspecting the same bytes twice (a repeat download)
reuses the same name, and two different files can never write over each other's pages.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .interfaces import IRenderer
from .models import Inspection
from .renderers import DocxRenderer, PdfRenderer, RasterRenderer, TextRenderer, XlsxRenderer, ZipRenderer
from .sniff import sniff_mime


class FileInspector:
    def __init__( self, root: Path, renderers: list[ IRenderer ] ) -> None:
        self._root, self._renderers = root, renderers

    def inspect( self, source: Path, sha256: str ) -> Inspection:
        mime = sniff_mime( source )
        renderer = next(( r for r in self._renderers if r.handles( mime )), None )
        if renderer is None:
            return Inspection( kind="binary", mime=mime,
                               note=f"no viewer for { mime }; the file is saved and its checksum is above" )
        out_dir = self._root / sha256[ :32 ]
        if out_dir.exists():
            shutil.rmtree( out_dir )
        out_dir.mkdir( parents=True )
        try:
            return renderer.render( source, mime, out_dir )
        except Exception as exc:                            # a damaged file is a note, never a crash of the download
            return Inspection( kind="binary", mime=mime,
                               note=f"the file could not be opened as { mime }: { type( exc ).__name__ }: { str( exc )[ :200 ] }" )


def default_inspector( root: Path ) -> FileInspector:
    """The composition root for inspection: the only place that names the renderers."""
    return FileInspector( root, [ RasterRenderer(), PdfRenderer(), TextRenderer(), DocxRenderer(), XlsxRenderer(),
                                  ZipRenderer()])

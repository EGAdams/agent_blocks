"""FileInspector: sniff, choose the first renderer that handles the type, write into one folder per file.

The folder is named by the file's sha256, so inspecting the same bytes twice (a repeat download)
reuses the same name, and two different files can never write over each other's pages.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .archive import SafeZipUnpacker
from .interfaces import IArchiveUnpacker, IRenderer
from .models import ArchiveMember, Inspection
from .renderers import DocxRenderer, PdfRenderer, RasterRenderer, TextRenderer, XlsxRenderer, ZipRenderer
from .sniff import sniff_mime


class FileInspector:
    def __init__( self, root: Path, renderers: list[ IRenderer ], unpacker: IArchiveUnpacker | None = None ) -> None:
        self._root, self._renderers, self._unpacker = root, renderers, unpacker

    def inspect( self, source: Path, sha256: str ) -> Inspection:
        result = self._render( source, sha256 )
        if result.kind != "archive" or self._unpacker is None:
            return result
        return self._unpack( source, sha256, result )

    def _unpack( self, source: Path, sha256: str, listing: Inspection ) -> Inspection:
        """Each readable member written out and inspected -- never unpacked further, even if it is a ZIP."""
        try:
            unpacked = self._unpacker.unpack( source, self._root / sha256[ :32 ] / "members" )
        except Exception as exc:                            # a damaged archive keeps its listing, says why
            return listing.model_copy( update={ "note": f"not unpacked: { type( exc ).__name__ }: { str( exc )[ :200 ]}" })
        members = []
        for m in unpacked.members:
            path = Path( m.path )
            digest = hashlib.sha256( path.read_bytes()).hexdigest()
            members.append( ArchiveMember( name=m.name, path=m.path, bytes=m.bytes, sha256=digest,
                                           inspection=self._render( path, digest )))
        lines = [ f"unpacked { len( members ) } file(s); { len( unpacked.skipped ) } left in the archive" ]
        lines += [ f"  left in: { s.name } -- { s.reason }" for s in unpacked.skipped[ :200 ]]
        if len( unpacked.skipped ) > 200:
            lines.append( f"  ... and { len( unpacked.skipped ) - 200 } more left in" )
        note = unpacked.note or lines[ 0 ]
        text = listing.text + "\n\n" + "\n".join( lines )
        return listing.model_copy( update={ "members": members, "note": note, "text": text[ :len( listing.text ) + 40_000 ]})

    def _render( self, source: Path, sha256: str ) -> Inspection:
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
                                  ZipRenderer()], SafeZipUnpacker())

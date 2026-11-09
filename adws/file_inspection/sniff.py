"""What a file really is, from its first bytes -- not from its name and not from the server's say-so.

A government site that has timed out a session answers a `.tif` link with its login page, and
Chrome saves that HTML under the `.tif` name. Trusting the extension would hand ChatGPT "a TIFF"
that is a web page; `mismatch` names that case so the download is not called a success.
"""
from __future__ import annotations

import mimetypes
import zipfile
from pathlib import Path

SNIFF_BYTES = 4096

#: (leading bytes, mime). Order matters only where one prefix contains another; none here do.
MAGIC: tuple[ tuple[ bytes, str ], ... ] = (
    ( b"II*\x00", "image/tiff" ),
    ( b"MM\x00*", "image/tiff" ),
    ( b"%PDF", "application/pdf" ),
    ( b"\x89PNG\r\n\x1a\n", "image/png" ),
    ( b"\xff\xd8\xff", "image/jpeg" ),
    ( b"GIF87a", "image/gif" ),
    ( b"GIF89a", "image/gif" ),
)

OFFICE = {
    "word/document.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/workbook.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt/presentation.xml": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

TEXT_TYPES = { ".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json",
               ".xml": "application/xml", ".html": "text/html", ".htm": "text/html", ".md": "text/markdown" }

#: Extensions whose files are never legitimately a web page or plain text.
BINARY_EXTENSIONS = frozenset({ ".tif", ".tiff", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                                ".zip", ".docx", ".xlsx", ".pptx", ".xls", ".doc" })


def sniff_mime( path: Path ) -> str:
    with path.open( "rb" ) as handle:
        head = handle.read( SNIFF_BYTES )
    for prefix, mime in MAGIC:
        if head.startswith( prefix ):
            return mime
    if head[ :4 ] == b"RIFF" and head[ 8:12 ] == b"WEBP":
        return "image/webp"
    if head[ :2 ] == b"BM" and path.suffix.lower() == ".bmp":
        return "image/bmp"
    if head.startswith( b"PK\x03\x04" ):
        return _zip_kind( path )
    if head.startswith( b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" ):    # OLE2: .doc, .xls -- which one is inside
        from .legacy import ole_kind
        return ole_kind( path )
    if _looks_textual( head ):
        if _looks_html( head ):
            return "text/html"
        return TEXT_TYPES.get( path.suffix.lower(), "text/plain" )
    guessed, _ = mimetypes.guess_type( path.name )
    return guessed or "application/octet-stream"


def mismatch( path: Path, mime: str ) -> str:
    """A sentence when the bytes contradict the name in a way that means the wrong thing was saved."""
    suffix = path.suffix.lower()
    if suffix in BINARY_EXTENSIONS and mime in ( "text/html", "text/plain" ):
        what = "a web page (HTML)" if mime == "text/html" else "plain text"
        return ( f"{ path.name } is { what }, not a { suffix } file -- the site most likely sent an error or "
                 "sign-in page instead of the document" )
    return ""


def _zip_kind( path: Path ) -> str:
    try:
        with zipfile.ZipFile( path ) as archive:
            names = set( archive.namelist())
    except ( zipfile.BadZipFile, OSError ):
        return "application/zip"
    for marker, mime in OFFICE.items():
        if marker in names:
            return mime
    return "application/zip"


def _looks_textual( head: bytes ) -> bool:
    if not head or b"\x00" in head:
        return False
    try:
        head.decode( "utf-8" )
        return True
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the sniff boundary is still text.
        return exc.start >= len( head ) - 3


def _looks_html( head: bytes ) -> bool:
    start = head.lstrip()[ :200 ].lower()
    return start.startswith(( b"<!doctype html", b"<html" )) or b"<html" in start

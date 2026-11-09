"""SafeZipUnpacker: take the readable files out of a downloaded ZIP without trusting anything it says.

A ZIP is a list of names and sizes written by whoever made it, and every one of those can lie. So:

* a name that climbs out of the folder (`../`, `/etc/x`, `C:\\x`, `\\\\server\\x`) is refused, and
  every destination is checked to be inside the folder after it is resolved, not only before;
* a symbolic link is refused (it could point anywhere once followed);
* an encrypted member is refused (there is no password to give it, and none is asked for);
* a program or script is **never** written out (`.exe`, `.ps1`, `.js`, `.sh`, `.lnk` ...): "never
  silently execute downloaded content" starts with never putting it where a double-click runs it;
* an archive inside the archive is listed, not opened -- one level only, so a nested bomb stays shut;
* sizes are counted while reading, never taken from the header: a member that inflates past what it
  declared, past `max_member_bytes`, or past `max_ratio` times its compressed size is deleted and
  refused, and the whole unpack stops at `max_total_bytes`;
* nothing written out is executable (mode 0600), and the original archive is never modified.

What it returns says, for every entry it did not unpack, why -- a refusal is a line in the answer, not
a silence.
"""
from __future__ import annotations

import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

#: Never written out, whatever the archive calls them. Lower-case suffixes.
PROGRAM_SUFFIXES = frozenset({
    ".exe", ".dll", ".com", ".scr", ".msi", ".msp", ".msc", ".cpl", ".sys", ".drv", ".ocx",
    ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".lnk", ".url", ".reg", ".inf", ".pif", ".application", ".appx", ".msix", ".gadget",
    ".sh", ".bash", ".zsh", ".py", ".pyw", ".pl", ".rb", ".php", ".jar", ".class", ".apk", ".app",
    ".deb", ".rpm", ".dmg", ".pkg", ".so", ".dylib", ".bin", ".elf", ".run", ".docm", ".xlsm", ".pptm",
    ".dotm", ".xltm",
})
#: Listed, never opened: one level of unpacking only.
ARCHIVE_SUFFIXES = frozenset({ ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".iso",
                               ".img", ".vhd", ".vhdx", ".arj", ".lz", ".lzh", ".z" })
#: Leading bytes of a program, caught even under an innocent name ("plat.pdf" that is really an .exe).
PROGRAM_MAGIC = ( b"MZ", b"\x7fELF", b"#!", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe" )
UNSAFE_CHARS = re.compile( r'[<>:"|?*\x00-\x1f]' )
CHUNK = 1 << 16


class UnpackLimits( BaseModel ):
    max_entries: int = 10_000                 # past this the archive is listed, nothing is unpacked
    max_members: int = 25                     # files written out; later ones are listed as over the limit
    max_member_bytes: int = 100 * 1024 * 1024
    max_total_bytes: int = 250 * 1024 * 1024
    max_ratio: int = 200                      # uncompressed / compressed, beyond which it is a bomb


class UnpackedMember( BaseModel ):
    name: str                                 # the path inside the archive, as the archive spells it
    path: str                                 # where it was written on this machine
    bytes: int


class SkippedMember( BaseModel ):
    name: str
    reason: str


class Unpacked( BaseModel ):
    members: list[ UnpackedMember ] = Field( default_factory=list )
    skipped: list[ SkippedMember ] = Field( default_factory=list )
    note: str = ""                            # a reason nothing at all was unpacked


class _Refused( Exception ):
    pass


class SafeZipUnpacker:
    def __init__( self, limits: UnpackLimits | None = None ) -> None:
        self._limits = limits or UnpackLimits()

    def unpack( self, source: Path, out_dir: Path ) -> Unpacked:
        out_dir.mkdir( parents=True, exist_ok=True )
        root = out_dir.resolve()
        result = Unpacked()
        with zipfile.ZipFile( source ) as archive:
            entries = archive.infolist()
            if len( entries ) > self._limits.max_entries:
                result.note = ( f"{ len( entries ):,} entries is more than the { self._limits.max_entries:,} this will "
                                "unpack, so it is listed only" )
                return result
            total = 0
            for entry in entries:
                if entry.is_dir():
                    continue
                reason = self._refusal( entry )
                if not reason and len( result.members ) >= self._limits.max_members:
                    reason = f"over the limit of { self._limits.max_members } files unpacked from one archive"
                if not reason and total + entry.file_size > self._limits.max_total_bytes:
                    reason = "unpacking it would pass the total size allowed for one archive"
                if reason:
                    result.skipped.append( SkippedMember( name=entry.filename, reason=reason ))
                    continue
                try:
                    target = self._destination( root, entry.filename )
                    written = self._write( archive, entry, target, self._limits.max_total_bytes - total )
                except _Refused as exc:
                    result.skipped.append( SkippedMember( name=entry.filename, reason=str( exc )))
                    continue
                total += written
                result.members.append( UnpackedMember( name=entry.filename, path=str( target ), bytes=written ))
        return result

    def _refusal( self, entry: zipfile.ZipInfo ) -> str:
        name = entry.filename
        if entry.flag_bits & 0x1:
            return "it is encrypted, and no password is asked for"
        if stat.S_ISLNK( entry.external_attr >> 16 ):
            return "it is a symbolic link, which could point anywhere"
        parts = PurePosixPath( name.replace( "\\", "/" )).parts
        if parts and parts[ 0 ] in ( "__MACOSX", ):
            return "Mac Finder bookkeeping, not a document"
        suffix = PurePosixPath( name.replace( "\\", "/" )).suffix.lower()
        if suffix in PROGRAM_SUFFIXES:
            return f"it is a program or script ({ suffix }), and those are never unpacked"
        if suffix in ARCHIVE_SUFFIXES:
            return "it is an archive inside the archive; only one level is unpacked"
        if entry.file_size > self._limits.max_member_bytes:
            return f"{ entry.file_size:,} bytes is over the { self._limits.max_member_bytes:,} allowed for one file"
        if entry.compress_size and entry.file_size / entry.compress_size > self._limits.max_ratio:
            return "it expands far more than real documents do (a compression bomb)"
        return ""

    @staticmethod
    def _destination( root: Path, name: str ) -> Path:
        unixy = name.replace( "\\", "/" )
        if unixy.startswith( "/" ) or re.match( r"^[A-Za-z]:", unixy ):
            raise _Refused( "its name is an absolute path, outside the folder" )
        parts = [ p for p in PurePosixPath( unixy ).parts if p not in ( "", "." )]
        if not parts or any( p == ".." for p in parts ):
            raise _Refused( "its name climbs out of the folder (..)" )
        clean = [ UNSAFE_CHARS.sub( "_", p ).rstrip( " ." ) or "_" for p in parts ]
        target = root.joinpath( *clean )
        stem, suffix, n = target.stem, target.suffix, 1
        while target.exists():
            n += 1
            target = target.with_name( f"{ stem } ({ n }){ suffix }" )
        if not target.resolve().is_relative_to( root ):
            raise _Refused( "its name resolves outside the folder" )
        return target

    def _write( self, archive: zipfile.ZipFile, entry: zipfile.ZipInfo, target: Path, room: int ) -> int:
        ceiling = min( entry.file_size, self._limits.max_member_bytes, room )
        ratio_cap = max( entry.compress_size, 1 ) * self._limits.max_ratio
        target.parent.mkdir( parents=True, exist_ok=True )
        written = 0
        try:
            with archive.open( entry ) as member, open( target, "xb" ) as out:
                os.chmod( target, 0o600 )
                while chunk := member.read( CHUNK ):
                    if written == 0 and chunk.startswith( PROGRAM_MAGIC ):
                        raise _Refused( "its bytes are a program or script, whatever its name says" )
                    written += len( chunk )
                    if written > ceiling or written > ratio_cap:
                        raise _Refused( "it inflated past the size it declared or the limits (a compression bomb)" )
                    out.write( chunk )
        except _Refused:
            target.unlink( missing_ok=True )
            raise
        except ( zipfile.BadZipFile, OSError, RuntimeError, ValueError, EOFError ) as exc:
            target.unlink( missing_ok=True )
            raise _Refused( f"it could not be read: { type( exc ).__name__ }: { str( exc )[ :120 ]}" ) from exc
        return written

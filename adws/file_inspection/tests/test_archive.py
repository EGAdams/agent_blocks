"""SafeZipUnpacker and FileInspector's unpacking: what comes out, and everything that must stay in."""
from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from file_inspection.archive import SafeZipUnpacker, UnpackLimits  # noqa: E402
from file_inspection.inspector import default_inspector  # noqa: E402

SHA = "cd" * 32


def make_zip( path: Path, entries: dict[ str, bytes ], method: int = zipfile.ZIP_DEFLATED ) -> Path:
    with zipfile.ZipFile( path, "w", method ) as archive:
        for name, data in entries.items():
            archive.writestr( name, data )
    return path


def reasons( result ) -> dict[ str, str ]:
    return { s.name: s.reason for s in result.skipped }


def test_documents_come_out_and_are_not_executable( tmp_path: Path ) -> None:
    source = make_zip( tmp_path / "deeds.zip", { "a/deed.txt": b"Liber 4 of Plats, page 31\n", "b.csv": b"x,y\n1,2\n" })
    before = source.read_bytes()
    result = SafeZipUnpacker().unpack( source, tmp_path / "out" )
    assert [ m.name for m in result.members ] == [ "a/deed.txt", "b.csv" ]
    deed = Path( result.members[ 0 ].path )
    assert deed.read_bytes() == b"Liber 4 of Plats, page 31\n"
    assert deed.is_relative_to( tmp_path / "out" )
    assert not deed.stat().st_mode & ( stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH )
    assert source.read_bytes() == before


def test_names_that_climb_out_are_refused_and_nothing_is_written_outside( tmp_path: Path ) -> None:
    source = make_zip( tmp_path / "evil.zip", { "../escape.txt": b"x", "/etc/abs.txt": b"x", "C:/win.txt": b"x",
                                                "ok/../../up.txt": b"x", "..\\back.txt": b"x", "fine.txt": b"ok" })
    result = SafeZipUnpacker().unpack( source, tmp_path / "out" )
    assert [ m.name for m in result.members ] == [ "fine.txt" ]
    assert set( reasons( result )) == { "../escape.txt", "/etc/abs.txt", "C:/win.txt", "ok/../../up.txt", "..\\back.txt" }
    assert sorted( p.name for p in tmp_path.iterdir()) == [ "evil.zip", "out" ]


def test_symlinks_encrypted_programs_and_nested_archives_stay_in( tmp_path: Path ) -> None:
    source = tmp_path / "mixed.zip"
    with zipfile.ZipFile( source, "w" ) as archive:
        link = zipfile.ZipInfo( "link" )
        link.external_attr = ( stat.S_IFLNK | 0o777 ) << 16
        archive.writestr( link, "/etc/passwd" )
        archive.writestr( "locked.txt", b"secret" )
        archive.writestr( "setup.exe", b"MZ..." )
        archive.writestr( "run.PS1", b"Write-Host hi" )
        archive.writestr( "inner.zip", b"PK\x03\x04" )
        archive.writestr( "plat.pdf", b"MZ\x90\x00 disguised program" )
        archive.writestr( "__MACOSX/._plat.pdf", b"junk" )
        archive.writestr( "readme.txt", b"hello" )
    # zipfile cannot encrypt, so mark locked.txt encrypted in the central directory (flags at offset 8).
    data = bytearray( source.read_bytes())
    entry = data.find( b"PK\x01\x02", data.find( b"PK\x01\x02" ) + 1 )
    assert data[ entry + 46:entry + 56 ] == b"locked.txt"
    data[ entry + 8 ] |= 0x1
    source.write_bytes( bytes( data ))
    result = SafeZipUnpacker().unpack( source, tmp_path / "out" )
    assert [ m.name for m in result.members ] == [ "readme.txt" ]
    why = reasons( result )
    assert "symbolic link" in why[ "link" ]
    assert "encrypted" in why[ "locked.txt" ]
    assert "program" in why[ "setup.exe" ] and "program" in why[ "run.PS1" ]
    assert "archive inside" in why[ "inner.zip" ]
    assert "program" in why[ "plat.pdf" ]
    assert not ( tmp_path / "out" / "plat.pdf" ).exists()      # written then caught, so removed


def test_a_compression_bomb_is_refused( tmp_path: Path ) -> None:
    source = make_zip( tmp_path / "bomb.zip", { "zeros.txt": b"\0" * 5_000_000, "ok.txt": b"fine" })
    result = SafeZipUnpacker().unpack( source, tmp_path / "out" )
    assert [ m.name for m in result.members ] == [ "ok.txt" ]
    assert "bomb" in reasons( result )[ "zeros.txt" ]


def test_a_member_that_lies_about_its_size_is_cut_off( tmp_path: Path ) -> None:
    source = make_zip( tmp_path / "liar.zip", { "big.txt": b"a" * 50_000 }, zipfile.ZIP_STORED )
    data = bytearray( source.read_bytes())
    # Patch the central directory's uncompressed size (offset 24 in the entry) down to 10 bytes.
    central = data.rfind( b"PK\x01\x02" )
    data[ central + 24:central + 28 ] = ( 10 ).to_bytes( 4, "little" )
    source.write_bytes( bytes( data ))
    result = SafeZipUnpacker().unpack( source, tmp_path / "out" )
    assert not result.members
    assert "big.txt" in reasons( result )
    assert not any(( tmp_path / "out" ).rglob( "big.txt" ))


def test_limits_on_count_size_and_entries( tmp_path: Path ) -> None:
    source = make_zip( tmp_path / "many.zip", { f"n{ i }.txt": b"x" * 100 for i in range( 6 )})
    few = SafeZipUnpacker( UnpackLimits( max_members=4 )).unpack( source, tmp_path / "a" )
    assert len( few.members ) == 4 and all( "over the limit" in r for r in reasons( few ).values())
    small = SafeZipUnpacker( UnpackLimits( max_total_bytes=250 )).unpack( source, tmp_path / "b" )
    assert len( small.members ) == 2
    listed = SafeZipUnpacker( UnpackLimits( max_entries=3 )).unpack( source, tmp_path / "c" )
    assert not listed.members and "listed only" in listed.note


def test_inspector_unpacks_one_level_and_inspects_each_member( tmp_path: Path ) -> None:
    inner = make_zip( tmp_path / "inner.zip", { "deep.txt": b"never opened" })
    source = make_zip( tmp_path / "outer.zip", { "notes.txt": b"Fralick's Addition", "data.csv": b"lot,block\n1,2\n",
                                                 "inner.zip": inner.read_bytes(), "tool.exe": b"MZ" })
    result = default_inspector( tmp_path / "insp" ).inspect( source, SHA )
    assert result.kind == "archive" and result.page_count == 4
    assert [ m.name for m in result.members ] == [ "notes.txt", "data.csv" ]
    notes = result.members[ 0 ]
    assert notes.inspection.kind == "text" and "Fralick" in notes.inspection.text
    assert len( notes.sha256 ) == 64 and notes.inspection.members == []
    assert "left in: inner.zip" in result.text and "left in: tool.exe" in result.text
    assert result.note.startswith( "unpacked 2 file(s)" )
    assert not any(( tmp_path / "insp" ).rglob( "deep.txt" ))


def test_a_damaged_zip_keeps_its_note_and_does_not_crash( tmp_path: Path ) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes( b"PK\x03\x04" + b"\0" * 40 )
    result = default_inspector( tmp_path / "insp" ).inspect( bad, SHA )
    assert result.members == []

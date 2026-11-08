"""DownloadFiler: a repeat download never overwrites the earlier file, and a name cannot climb out."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from browser_workflows.playback.downloads import DownloadFiler, FileDownloadVerifier, safe_name  # noqa: E402


def test_same_name_twice_keeps_both( tmp_path: Path ) -> None:
    filer = DownloadFiler( poll_seconds=0.01 )
    kept_names = []
    for body in ( b"first", b"second" ):
        staged = filer.stage( str( tmp_path ))
        before = staged.before()
        Path( staged.chrome_dir, "0337201.tif" ).write_bytes( body )
        files = staged.wait_new( before, timeout_seconds=5 )
        kept = staged.keep( files )
        assert staged.finish() == []
        kept_names.append( kept[ 0 ].name )
    assert kept_names == [ "0337201.tif", "0337201 (2).tif" ]
    assert ( tmp_path / "0337201.tif" ).read_bytes() == b"first"
    assert ( tmp_path / "0337201 (2).tif" ).read_bytes() == b"second"
    assert list(( tmp_path / ".incoming" ).iterdir()) == []


def test_names_are_made_safe() -> None:
    assert safe_name( "..\\..\\Windows\\evil.exe" ) == "evil.exe"
    assert safe_name( "../../etc/passwd" ) == "passwd"
    assert safe_name( "CON.txt" ) == "_CON.txt"
    assert safe_name( 'a<b>c:"d|e?.pdf' ) == "a_b_c__d_e_.pdf"
    assert safe_name( "..." ) == "download"
    assert len( safe_name( "x" * 400 + ".tif" )) <= 150


def test_verifier_refuses_html_saved_as_tif( tmp_path: Path ) -> None:
    fake = tmp_path / "plat.tif"
    fake.write_text( "<html><body>Session expired</body></html>" )
    assert "not a .tif" in FileDownloadVerifier().problem( fake )
    real = tmp_path / "real.tif"
    real.write_bytes( b"II*\x00" + b"\x00" * 32 )
    assert FileDownloadVerifier().problem( real ) == ""

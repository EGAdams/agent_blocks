"""Offline tests for Scala's machine-aware storage. Run from lancedb_memory/:

    .venv/bin/python -m scala.tests.test_storage

No scanner, no network, no ssh login: the scan source is a fake, every cabinet is a tmpdir, and the destination
helper runs for real as a local subprocess (the same code ssh carries to BATI and R46). "Unreachable" is a real
ssh to a closed loopback port, "wrong machine" is a target whose configured hostname is not this one.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from scala.storage.interfaces import StorageRefused, TargetUnreachable
from scala.storage.records import JsonlAudit, JsonRunRecord, StagedPending
from scala.storage.service import ScanStorage, filing_name
from scala.storage.targets import JsonTargetRegistry, resolve_folder
from scala.storage.transports import HelperTransport, SshOrLocalTransports

HERE    = socket.gethostname()
SCAN    = "scan_01M787R1STEA7N30DM8PG13MSZ"
SCAN2   = "scan_01M787R1STEA7N30DM8PG13MSA"
BYTES   = b"\xff\xd8\xff\xe0 synthetic jpeg " * 1000
REPO    = Path( __file__ ).resolve().parents[ 3 ]


class FakeSource:
    def __init__( self ) -> None:
        self.scans = { SCAN: BYTES, SCAN2: b"%PDF-1.4 other scan" }
        self.down = False
        self.reads = 0

    def _meta( self, scan_id: str ) -> dict:
        data = self.scans[ scan_id ]
        mime = "application/pdf" if data.startswith( b"%PDF" ) else "image/jpeg"
        return { "scan_id": scan_id, "mime_type": mime, "size_bytes": len( data ),
                 "sha256": hashlib.sha256( data ).hexdigest(), "captured_at": "2026-11-12T15:59:30.405Z" }

    def document( self, scan_id: str ) -> dict:
        if self.down:
            raise StorageRefused( "scanner_unreachable", "scanner down" )
        if scan_id not in self.scans:
            raise StorageRefused( "scanner_not_found", "no such scan" )
        return self._meta( scan_id )

    def original( self, scan_id: str ) -> bytes:
        self.document( scan_id )
        self.reads += 1
        return self.scans[ scan_id ]

    def latest( self ) -> str:
        return SCAN


class Rig:
    """One tmp world: three cabinets, a config naming them, data dir, and a ScanStorage over all of it."""

    def __init__( self ) -> None:
        self.tmp = Path( tempfile.mkdtemp( prefix="scala_storage_" ))
        self.roots = { m: self.tmp / m.lower() / "file_cabinet" for m in ( "HOME", "WRONG", "AWAY" )}
        for root in self.roots.values():
            root.mkdir( parents=True )
        folders = [ "insurance/home", "bills/electric", "taxes" ]
        config = { "targets": {
            "HOME":  { "hostname": HERE, "description": "this machine", "aliases": [ "home", "mom" ],
                       "root": str( self.roots[ "HOME" ]), "folders": folders },
            "WRONG": { "hostname": "not-" + HERE, "description": "misconfigured: points at this machine",
                       "root": str( self.roots[ "WRONG" ]), "folders": folders },
            "AWAY":  { "hostname": "away-host", "description": "offline", "root": str( self.roots[ "AWAY" ]),
                       "ssh": { "host": "127.0.0.1", "port": 9, "user": "nobody" }, "folders": folders }}}
        self.config = self.tmp / "targets.json"
        self.config.write_text( json.dumps( config ))
        self.data = self.tmp / "data"
        self.source = FakeSource()
        self.registry = JsonTargetRegistry( self.config )

    def storage( self, transports=None, adw_id: str = "t0000001" ) -> ScanStorage:
        return ScanStorage( self.source, self.registry, transports or LocalForWrong(), JsonlAudit( self.data / "audit.jsonl" ),
                            JsonRunRecord( self.data / "runs" / f"{ adw_id }.json", adw_id=adw_id ),
                            StagedPending( self.data / "pending" ), self.data / "retrieved", adw_id=adw_id )

    def audit( self ) -> list[ dict ]:
        path = self.data / "audit.jsonl"
        return [ json.loads( l ) for l in path.read_text().splitlines()] if path.exists() else []


class LocalForWrong( SshOrLocalTransports ):
    """WRONG is run locally on purpose: the address is right but the machine is not the one expected."""

    def for_target( self, target ):
        if target.machine == "WRONG":
            return HelperTransport( target, [ sys.executable ], remote=False, timeout=30 )
        return super().for_target( target )


class FlakyTransports( LocalForWrong ):
    """HOME is unreachable while `down` is set -- the interrupted-transfer case, retried later."""

    def __init__( self ) -> None:
        super().__init__()
        self.down = True

    def for_target( self, target ):
        real = super().for_target( target )
        rig = self

        class Maybe:
            def call( self, op, args, data=None ):
                if rig.down:
                    raise TargetUnreachable( f"{ target.machine } dropped the connection" )
                return real.call( op, args, data )
        return Maybe()


def refused( fn, code: str ) -> StorageRefused:
    try:
        fn()
    except StorageRefused as exc:
        assert exc.code == code, ( exc.code, exc.message )
        return exc
    raise AssertionError( f"expected refusal { code }" )


# -- tests -------------------------------------------------------------------------------------------------------

def test_store_copies_verifies_and_preserves() -> None:
    rig = Rig()
    out = rig.storage().store_scan( SCAN, "HOME", "insurance/home" )
    assert out.ok and out.verified, out.line
    name = "2026-11-12_home_" + hashlib.sha256( BYTES ).hexdigest()[ :12 ] + ".jpg"
    dest = rig.roots[ "HOME" ] / "insurance" / "home" / name
    assert out.data[ "filename" ] == name and out.data[ "destination" ] == str( dest )
    assert dest.read_bytes() == BYTES and out.data[ "sha256" ] == hashlib.sha256( BYTES ).hexdigest()
    assert out.data[ "original_preserved" ] and rig.source.scans[ SCAN ] == BYTES
    assert out.data[ "hostname" ] == HERE and out.data[ "status" ] == "stored"
    assert not list(( rig.roots[ "HOME" ] / "insurance" / "home" ).glob( ".*part" ))
    row = rig.audit()[ -1 ]
    for key in ( "scan_id", "source", "machine", "destination", "sha256", "result" ):
        assert row[ key ], key
    assert row[ "result" ] == "stored"
    run = json.loads(( rig.data / "runs" / "t0000001.json" ).read_text())
    assert [ p[ "phase" ] for p in run[ "phases" ]] == [ "resolved", "fetched", "transferring", "verifying", "done" ]


def test_natural_language_machine_folder_and_last_scan() -> None:
    rig = Rig()
    out = rig.storage().store_scan( "the last scan", "Mom's", "/" + str( rig.roots[ "HOME" ]).strip( "/" ) + "/insurance/home/" )
    assert out.verified and out.data[ "scan_id" ] == SCAN and out.data[ "folder" ] == "insurance/home"
    assert resolve_folder( rig.registry.resolve( "home" ), "electric" ) == "bills/electric"


def test_duplicate_is_idempotent_not_overwrite() -> None:
    rig = Rig()
    storage = rig.storage()
    first = storage.store_scan( SCAN, "HOME", "insurance/home" )
    dest = Path( first.data[ "destination" ])
    mtime = dest.stat().st_mtime_ns
    second = storage.store_scan( SCAN, "HOME", "insurance/home" )
    assert second.verified and second.data[ "status" ] == "duplicate" and dest.stat().st_mtime_ns == mtime
    assert len( list( dest.parent.iterdir())) == 1


def test_name_conflict_never_overwrites() -> None:
    rig = Rig()
    first = rig.storage().store_scan( SCAN, "HOME", "insurance/home" )
    dest = Path( first.data[ "destination" ])
    os.chmod( dest, 0o644 )
    dest.write_bytes( b"someone else's file" )
    refused( lambda: rig.storage().store_scan( SCAN, "HOME", "insurance/home" ), "name_conflict" )
    assert dest.read_bytes() == b"someone else's file"
    assert [ r[ "result" ] for r in rig.audit()] == [ "stored", "refused:name_conflict" ]     # audited once, not twice


def test_wrong_machine_is_refused_and_nothing_written() -> None:
    rig = Rig()
    exc = refused( lambda: rig.storage().store_scan( SCAN, "WRONG", "insurance/home" ), "identity_mismatch" )
    assert exc.extra.get( "hostname" ) == HERE
    assert not any( rig.roots[ "WRONG" ].rglob( "*.jpg" )) and not any( rig.roots[ "HOME" ].rglob( "*.jpg" ))
    refused( lambda: rig.storage().store_scan( SCAN, "BATI-ish", "insurance/home" ), "unknown_machine" )


def test_unreachable_keeps_original_and_staged_copy_for_retry() -> None:
    rig = Rig()
    out = rig.storage().store_scan( SCAN, "AWAY", "taxes" )
    assert not out.ok and out.retryable and out.data[ "original_preserved" ]
    assert Path( out.data[ "pending_copy" ]).read_bytes() == BYTES
    assert rig.audit()[ -1 ][ "result" ] == "unreachable"
    status = rig.storage().transfer_status( SCAN )
    assert status.data[ "pending" ] and "waiting for retry" in status.line
    health = rig.storage().storage_health( "AWAY" )
    assert not health.ok and health.retryable and health.data[ "reachable" ] is False


def test_interrupted_transfer_then_retry_from_staged_copy() -> None:
    rig = Rig()
    flaky = FlakyTransports()
    first = rig.storage( flaky ).store_scan( SCAN, "HOME", "bills/electric" )
    assert not first.ok and first.retryable
    # a half-written .part from the dropped connection, aged past the stale limit
    folder = rig.roots[ "HOME" ] / "bills" / "electric"
    folder.mkdir( parents=True, exist_ok=True )
    stale = folder / ( "." + filing_name( rig.source.document( SCAN ), "bills/electric" ) + ".dead.part" )
    stale.write_bytes( BYTES[ :100 ])
    os.utime( stale, ( time.time() - 3600, time.time() - 3600 ))
    flaky.down = False
    again = rig.storage( flaky, "t0000002" ).store_scan( SCAN, "HOME", "bills/electric" )
    assert again.verified, again.line
    assert not stale.exists()
    assert list(( rig.data / "pending" ).glob( "*" )) == []
    assert ( folder / again.data[ "filename" ]).read_bytes() == BYTES


def test_retry_from_staged_copy_while_scanner_down_is_not_claimed_as_success() -> None:
    rig = Rig()
    flaky = FlakyTransports()
    assert rig.storage( flaky ).store_scan( SCAN, "HOME", "taxes" ).retryable
    flaky.down, rig.source.down = False, True
    again = rig.storage( flaky, "t0000002" ).store_scan( SCAN, "HOME", "taxes" )
    assert Path( again.data[ "destination" ]).read_bytes() == BYTES          # the staged bytes were good
    assert not again.ok and not again.data[ "original_preserved" ] and "ORIGINAL NOT CONFIRMED" in again.line


def test_unauthorized_folder_traversal_and_absolute_escape() -> None:
    rig = Rig()
    storage = rig.storage()
    refused( lambda: storage.store_scan( SCAN, "HOME", "secret" ), "folder_not_allowed" )
    refused( lambda: storage.store_scan( SCAN, "HOME", "insurance/../../etc" ), "path_traversal" )
    refused( lambda: storage.store_scan( SCAN, "HOME", "~/x" ), "path_traversal" )
    refused( lambda: storage.store_scan( SCAN, "HOME", "insurance\\home" ), "path_traversal" )
    refused( lambda: storage.store_scan( SCAN, "HOME", "/etc/insurance/home" ), "absolute_path_refused" )
    refused( lambda: storage.store_scan( SCAN, "HOME", "insurance" ), "folder_not_allowed" )
    assert rig.source.reads == 0                       # refused before the original was even read
    rows = rig.audit()
    assert [ r[ "result" ] for r in rows ] == [ "refused:folder_not_allowed", "refused:path_traversal", "refused:path_traversal",
                                                "refused:path_traversal", "refused:absolute_path_refused",
                                                "refused:folder_not_allowed" ], rows


def test_symlink_escape_refused() -> None:
    rig = Rig()
    outside = rig.tmp / "outside"
    outside.mkdir()
    ( rig.roots[ "HOME" ] / "insurance" ).symlink_to( outside, target_is_directory=True )
    refused( lambda: rig.storage().store_scan( SCAN, "HOME", "insurance/home" ), "symlink_refused" )
    assert list( outside.rglob( "*" )) == []
    # and a symlinked file sitting where the copy would go
    ( rig.roots[ "HOME" ] / "insurance" ).unlink()
    target = rig.roots[ "HOME" ] / "taxes"
    target.mkdir()
    name = filing_name( rig.source.document( SCAN ), "taxes" )
    ( target / name ).symlink_to( outside / "victim" )
    refused( lambda: rig.storage().store_scan( SCAN, "HOME", "taxes" ), "symlink_refused" )
    assert not ( outside / "victim" ).exists()


def test_hash_mismatch_from_source_and_in_transit() -> None:
    rig = Rig()

    class Lying( FakeSource ):
        def original( self, scan_id ):
            return super().original( scan_id ) + b"!"
    rig.source = Lying()
    refused( lambda: rig.storage().store_scan( SCAN, "HOME", "taxes" ), "hash_mismatch" )
    # the helper itself refuses bytes that do not match what was announced
    target = Rig().registry.resolve( "HOME" )
    transport = SshOrLocalTransports().for_target( target )
    try:
        transport.call( "put", { "folder": "taxes", "name": "2026-11-12_taxes_000000000000.jpg",
                                 "sha256": "0" * 64, "size": 3 }, b"abc" )
    except StorageRefused as exc:
        assert exc.code == "hash_mismatch", exc.code
    else:
        raise AssertionError( "helper accepted mismatched bytes" )
    try:
        transport.call( "put", { "folder": "taxes", "name": "2026-11-12_taxes_000000000000.jpg",
                                 "sha256": hashlib.sha256( b"abcd" ).hexdigest(), "size": 4 }, b"abc" )
    except StorageRefused as exc:
        assert exc.code == "incomplete_transfer", exc.code
    else:
        raise AssertionError( "helper accepted a short transfer" )


def test_move_is_refused() -> None:
    rig = Rig()
    refused( lambda: rig.storage().store_scan( SCAN, "HOME", "taxes", copy=False ), "move_refused" )


def test_verify_retrieve_folders_health_targets() -> None:
    rig = Rig()
    storage = rig.storage()
    stored = storage.store_scan( SCAN2, "HOME", "taxes" )
    assert stored.data[ "filename" ].endswith( ".pdf" )
    assert storage.verify_scan( SCAN2, "HOME", "taxes" ).verified
    assert not storage.verify_scan( SCAN, "HOME", "taxes" ).ok              # never filed there
    got = storage.retrieve_scan( SCAN2, "HOME" )
    assert got.verified and Path( got.data[ "local_copy" ]).read_bytes() == rig.source.scans[ SCAN2 ]
    # retrieve also finds a file the audit trail does not know about, by hash
    rig.data.joinpath( "audit.jsonl" ).unlink()
    assert rig.storage().retrieve_scan( SCAN2, "HOME" ).verified
    refused( lambda: rig.storage().retrieve_scan( SCAN, "HOME" ), "not_found" )
    folders = storage.list_allowed_folders( "home" )
    assert folders.ok and { f[ "folder" ] for f in folders.data[ "folders" ]} == { "insurance/home", "bills/electric", "taxes" }
    health = storage.storage_health( "HOME" )
    assert health.ok and health.data[ "hostname" ] == HERE and health.data[ "writable" ]
    assert not storage.storage_health( "WRONG" ).ok
    assert [ t[ "machine" ] for t in storage.list_storage_targets().data[ "targets" ]] == [ "HOME", "WRONG", "AWAY" ]


def test_real_config_names_three_distinct_machines() -> None:
    registry = JsonTargetRegistry()
    by = { t.machine: t for t in registry.targets()}
    assert set( by ) == { "BATI", "R46", "SQMC" }
    assert by[ "BATI" ].hostname == "DESKTOP-SHDBATI" and by[ "BATI" ].root == "/home/adamsl/file_cabinet"
    assert by[ "SQMC" ].hostname == "DESKTOP-2OBSQMC" and by[ "R46" ].hostname == "Rosemary46"
    assert "insurance/home" in by[ "BATI" ].folders
    assert "rol_receipts" not in by[ "SQMC" ].folders and "inbox" not in by[ "SQMC" ].folders   # Mazda's and Scala's own
    assert registry.resolve( "Mom's" ).machine == "R46"
    assert len({ t.hostname.lower() for t in registry.targets()}) == 3


def test_adw_refuses_cleanly_and_writes_json_out() -> None:
    rig = Rig()
    out = rig.tmp / "out.json"
    env = { **os.environ, "SCALA_STORAGE_TARGETS": str( rig.config ), "SCALA_STORAGE_DATA": str( rig.data ),
            "HOME": str( rig.tmp )}                    # no scanner token here, on purpose
    done = subprocess.run([ sys.executable, str( REPO / "adws" / "adw_scala_storage.py" ), "storage.store",
                            "--scan-id", SCAN, "--machine", "HOME", "--folder", "../etc", "--json-out", str( out )],
                          capture_output=True, text=True, env=env, timeout=60 )
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stdout.startswith( "adw_id: " )
    body = json.loads( out.read_text())
    assert body[ "ok" ] is False and body[ "data" ][ "error" ] == "path_traversal"
    health = subprocess.run([ sys.executable, str( REPO / "adws" / "adw_scala_storage.py" ), "storage.health",
                              "--machine", "HOME", "--json-out", str( out )], capture_output=True, text=True, env=env, timeout=60 )
    assert health.returncode == 0, health.stdout + health.stderr
    assert json.loads( out.read_text())[ "data" ][ "hostname" ] == HERE


if __name__ == "__main__":
    failed = 0
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ) and callable( fn ):
            try:
                fn()
                print( f"ok   { name }" )
            except Exception as exc:                  # noqa: BLE001 -- report every test
                failed += 1
                print( f"FAIL { name }: { type( exc ).__name__ }: { exc }" )
    print( "all passed" if not failed else f"{ failed } failed" )
    sys.exit( 1 if failed else 0 )

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic"]
# ///
"""ADW Scala Storage -- Scala files, finds and verifies scans on BATI, R46 and SQMC. One operation per run.

    uv run adws/adw_scala_storage.py storage.targets
    uv run adws/adw_scala_storage.py storage.folders  --machine BATI
    uv run adws/adw_scala_storage.py storage.health   --machine R46
    uv run adws/adw_scala_storage.py storage.store    --scan-id scan_01M787R1STEA7N30DM8PG13MSZ --machine BATI --folder insurance/home
    uv run adws/adw_scala_storage.py storage.verify   --scan-id scan_... --machine BATI --folder insurance/home
    uv run adws/adw_scala_storage.py storage.retrieve --scan-id scan_... --machine SQMC
    uv run adws/adw_scala_storage.py storage.status   [--scan-id scan_...]

It runs on SQMC, the machine that holds the scanner API: the original's bytes are read there by scan id
(`~/.config/scan_bridge/worker_token`, loopback only) and carried to the destination by
`lancedb_memory/scala/storage/` -- in place when SQMC is the destination, over the existing ssh keys otherwise.
`--scan-id last` files the newest captured scan. A store is always a copy.

Every run prints `adw_id: xxxxxxxx` first, keeps a run record rewritten at each phase
(adws/adw_data/scala_storage/runs/<adw_id>.json) and adds audit lines (audit.jsonl). `--json-out` writes
{ok, verified, line, retryable, data}. Exit status: 0 verified, 1 not done, 2 refused.
"""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))

from scala.storage.interfaces import StorageRefused                      # noqa: E402
from scala.storage.models import StorageOutcome                          # noqa: E402

OPERATIONS = ( "targets", "folders", "health", "store", "verify", "retrieve", "status" )
DATA_DIR   = Path( os.environ.get( "SCALA_STORAGE_DATA", _HERE / "adw_data" / "scala_storage" ))
SCAN_URL   = os.environ.get( "SCAN_BRIDGE_URL", "http://127.0.0.1:8765/api/scan-bridge/v1" )
SCAN_TOKEN = Path.home() / ".config" / "scan_bridge" / "worker_token"


class _NoScanner:
    """The scan source on a machine without the scanner API: every scan read is refused, by name."""

    def _no( self, *_: object ) -> None:
        raise StorageRefused( "scanner_unavailable", "this machine has no scanner API token; Scala reads originals on SQMC" )

    document = original = latest = _no


def build_storage( adw_id: str, task_id: str, client: str ):
    """The composition root: the only place the concretes are named together."""
    from scala.storage.records import JsonlAudit, JsonRunRecord, StagedPending
    from scala.storage.scan_source import FacadeScanSource
    from scala.storage.service import ScanStorage
    from scala.storage.targets import JsonTargetRegistry
    from scala.storage.transports import SshOrLocalTransports

    if SCAN_TOKEN.exists():
        from agent_bridge_worker.adapters.http_scan_facade import HttpScanFacade, load_scan_token
        source = FacadeScanSource( HttpScanFacade( SCAN_URL, load_scan_token( SCAN_TOKEN ), timeout_seconds=60 ))
    else:
        source = _NoScanner()
    run = JsonRunRecord( DATA_DIR / "runs" / f"{ adw_id }.json", adw_id=adw_id, task_id=task_id, client=client )
    return ScanStorage( source, JsonTargetRegistry(), SshOrLocalTransports(), JsonlAudit( DATA_DIR / "audit.jsonl" ),
                        run, StagedPending( DATA_DIR / "pending" ), DATA_DIR / "retrieved",
                        adw_id=adw_id, task_id=task_id, client=client ), run


def dispatch( storage, op: str, args: argparse.Namespace ) -> StorageOutcome:
    table = {
        "targets":  lambda: storage.list_storage_targets(),
        "folders":  lambda: storage.list_allowed_folders( args.machine ),
        "health":   lambda: storage.storage_health( args.machine ),
        "store":    lambda: storage.store_scan( args.scan_id, args.machine, args.folder, copy=not args.move ),
        "verify":   lambda: storage.verify_scan( args.scan_id, args.machine, args.folder ),
        "retrieve": lambda: storage.retrieve_scan( args.scan_id, args.machine ),
        "status":   lambda: storage.transfer_status( args.scan_id ),
    }
    return table[ op ]()


def main( argv: list[ str ]) -> int:
    parser = argparse.ArgumentParser( prog="adw_scala_storage.py", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "operation", choices=[ f"storage.{ op }" for op in OPERATIONS ])
    parser.add_argument( "--machine", default="" )
    parser.add_argument( "--folder", default="" )
    parser.add_argument( "--scan-id", "--scan_id", dest="scan_id", default="" )
    parser.add_argument( "--move", action="store_true", help="refused: Scala only copies" )
    parser.add_argument( "--task-id", "--task_id", dest="task_id", default="" )
    parser.add_argument( "--client", default="" )
    parser.add_argument( "--json-out", "--json_out", dest="json_out", default="" )
    args = parser.parse_args( argv )

    adw_id = uuid.uuid4().hex[ :8 ]
    print( f"adw_id: { adw_id }", flush=True )
    op = args.operation.split( ".", 1 )[ 1 ]
    storage, run = build_storage( adw_id, args.task_id, args.client )
    try:
        outcome = dispatch( storage, op, args )
        status = 0 if outcome.ok and outcome.verified else 1
    except StorageRefused as exc:
        run.phase( "refused", operation=op, error=exc.code )
        outcome = StorageOutcome( ok=False, line=f"refused ({ exc.code }): { exc.message }",
                                  data={ "error": exc.code, **{ k: v for k, v in exc.extra.items() if k != "content_b64" }})
        status = 2
    print( outcome.line )
    if args.json_out:
        Path( args.json_out ).write_text( json.dumps({ **outcome.model_dump( mode="json" ), "adw_id": adw_id }, indent=1 ),
                                          encoding="utf-8" )
    return status


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))

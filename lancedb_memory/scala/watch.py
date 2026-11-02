"""Scala's ear on the scanners: watch `incoming_scans/` and file each new scan.

Neither scanner pushes anything -- the dashboard pulls a page when someone
presses Start Scan, runs Mazda's intake classifier, and stages a uniquely
named copy into `rol_finances/tools/receipt_scanning_tools/incoming_scans/`.
That staged copy is what this watches, so the dashboard and Mazda are not
changed at all, and Mazda has always had her look first.

* A scan is taken once it is `--settle` seconds old (default 20), giving the
  dashboard time to write Mazda's verdict next to it.
* A watermark (`~/scala/watermark.json`) remembers the newest scan handled.
  The first start sets it to *now*: the hundreds of scans already in the
  folder are Mazda's history, not a backlog. To file an old one, run the ADW
  on it by hand.
* Each scan is one run of `adws/adw_interface_scala_file_scan.py`, so every
  filing has its own ADW log. A run that fails is logged and passed over;
  the watcher never retries a scan forever.

    cd lancedb_memory && .venv/bin/python -m scala.watch [--once] [--settle 20] [--poll 5]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from .ledger import default_home

SCAN_SUFFIXES = frozenset({ ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".pdf" })
_LANCEDB = Path( __file__ ).resolve().parent.parent
_REPO = _LANCEDB.parent
ADW = _REPO / "adws" / "adw_interface_scala_file_scan.py"


def default_watch_dir() -> Path:
    return Path( os.environ.get( "SCALA_WATCH_DIR",
                                 Path.home() / "rol_finances" / "tools" / "receipt_scanning_tools" / "incoming_scans" ))


class Watermark:
    def __init__( self, home: Path ) -> None:
        self._path = home / "watermark.json"
        self._home = home

    def read( self ) -> float | None:
        try:
            return float( json.loads( self._path.read_text())[ "mtime" ])
        except ( OSError, ValueError, KeyError, TypeError ):
            return None

    def write( self, mtime: float ) -> None:
        self._home.mkdir( parents=True, exist_ok=True )
        os.chmod( self._home, 0o700 )
        partial = self._path.with_suffix( ".part" )
        partial.write_text( json.dumps({ "mtime": mtime }))
        os.replace( partial, self._path )


class ScanWatcher:
    def __init__( self,
                  watch_dir: Path,
                  watermark: Watermark,
                  run_scan:  Callable[[ Path ], None ],
                  settle_sec: float = 20.0,
                  clock: Callable[[], float ] = time.time ) -> None:
        self._dir = watch_dir
        self._watermark = watermark
        self._run_scan = run_scan
        self._settle = settle_sec
        self._clock = clock

    def tick( self ) -> list[ Path ]:
        """Hand every settled, unhandled scan to `run_scan`, oldest first."""
        mark = self._watermark.read()
        if mark is None:
            self._watermark.write( self._clock())
            return []
        try:
            entries = [ p for p in self._dir.iterdir()
                        if p.is_file() and p.suffix.lower() in SCAN_SUFFIXES ]
        except OSError:
            return []
        ready = sorted(( p.stat().st_mtime, p ) for p in entries
                       if mark < p.stat().st_mtime <= self._clock() - self._settle )
        handled: list[ Path ] = []
        for mtime, scan in ready:
            try:
                self._run_scan( scan )
            except Exception as exc:  # noqa: BLE001 -- one bad scan must not stop the ear
                print( f"[scala] { scan.name }: { type( exc ).__name__ }: { exc }", flush=True )
            self._watermark.write( mtime )
            handled.append( scan )
        return handled


def run_adw( scan: Path ) -> None:
    """One ADW run per scan. Its one-line summary goes to the service log."""
    out = default_home() / "last_line.txt"
    default_home().mkdir( parents=True, exist_ok=True )
    proc = subprocess.run(
        [ sys.executable, str( ADW ), "Scala: file a new scan", "--scan", str( scan ),
          "--message-out", str( out )],
        cwd=str( _REPO ), capture_output=True, text=True, timeout=600 )
    line = out.read_text().strip() if out.exists() else ""
    print( f"[scala] { line or f'ADW exited { proc.returncode } for { scan.name }' }", flush=True )
    if proc.returncode != 0 and not line:
        print( ( proc.stderr or proc.stdout )[ -800: ], flush=True )


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--once", action="store_true", help="one pass, then exit" )
    parser.add_argument( "--settle", type=float, default=20.0 )
    parser.add_argument( "--poll", type=float, default=5.0 )
    args = parser.parse_args( argv )
    watcher = ScanWatcher( default_watch_dir(), Watermark( default_home()), run_adw, settle_sec=args.settle )
    print( f"[scala] watching { default_watch_dir() }", flush=True )
    while True:
        watcher.tick()
        if args.once:
            return 0
        time.sleep( args.poll )


if __name__ == "__main__":
    sys.exit( main())

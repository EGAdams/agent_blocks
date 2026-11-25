#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Failure Triage -- one pass over every failed post-office request: group, route, mail, report.

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_failure_triage.py
    ... adws/adw_failure_triage.py --dry-run            reads and reports; writes nothing, sends nothing

Five phases, each one `TriagePass` method (adws/failure_triage/triage.py): collect, reconcile, route,
notify, report. It never resends a task and never fixes one; it sends a plan or a diagnosis to the owner
named in failure_triage/owners.json and one digest to EG through Yolanda. Read failure_triage/DESIGN.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))

from sssf_roles import ( IPhase, IPhaseHandle, ILifecycleLog, LifecycleLogged, ManualContextPhaseExecutor,   # noqa: E402
                         PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams                       # noqa: E402
from failure_triage.kit import build_pass                            # noqa: E402

PHASES = {
    "collect":   "read the ledger and every failed request envelope (read-only)",
    "reconcile": "fold the failures into clusters: new, grown, regressed, reopened, closed",
    "route":     "send each cluster that has no owner yet a plan or diagnose task",
    "notify":    "mail EG one digest of the clusters that owe mail",
    "report":    "save the ledger and write the one line",
}


class TriagePhase( LifecycleLogged, IPhase ):

    def __init__( self, name: str, step: Callable[ [], Any ], logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._name, self._step = name, step

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="failure-triage", description=PHASES[ self._name ])

    def act( self, handle: IPhaseHandle ) -> Any:
        result = self._step()
        handle.log( result=result if isinstance( result, ( int, str )) else "ok" )
        return result


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description="One failure-triage pass." )
    parser.add_argument( "--dry-run", action="store_true", help="write nothing, send nothing" )
    parser.add_argument( "--quiet-days", type=float, default=3 )
    parser.add_argument( "--repeat-at", type=int, default=2 )
    parser.add_argument( "--message-out", default="" )
    parser.add_argument( "--json-out", default="" )
    parser.add_argument( "--config", default=str( _HERE / "adw_sssf_config" / "sssf.config.yaml" ))
    parser.add_argument( "--adw-id", default=None )
    parsed = parser.parse_args( argv )

    log: ILifecycleLog = PrintLifecycleLog()
    loader = SssfConfigLoader( log )
    cfg = loader.load( parsed.config )
    loader.validate( cfg, [])
    run = SessionRunFactory( log ).create( cfg, parsed.adw_id )
    executor = ManualContextPhaseExecutor( log )

    triage = build_pass( dry_run=parsed.dry_run, quiet_days=parsed.quiet_days, repeat_at=parsed.repeat_at )
    steps = { "collect": triage.collect, "reconcile": triage.reconcile, "route": triage.route,
              "notify": triage.notify, "report": triage.report }
    for name, step in steps.items():
        executor.execute( run, TriagePhase( name, step, log ))
    report = triage.result

    answer = { "ok": report.ok, "verified": report.ok, "line": report.line, "retryable": False,
               "adw_id": getattr( run, "adw_id", parsed.adw_id or "" ),
               "data": { "counts": report.counts, "routed": report.routed, "route_errors": report.route_errors,
                         "mailed": report.mailed, "read": report.read, "unreadable": report.unreadable,
                         "changes": [{ "kind": c.kind, "cluster": c.cluster, "added": c.added } for c in report.changes ]}}
    if parsed.message_out:
        Path( parsed.message_out ).write_text( report.line + "\n" )
    if parsed.json_out:
        Path( parsed.json_out ).write_text( json.dumps( answer, indent=2, default=str ))
    print( report.line )
    return run.finish( accepted=report.ok, reason=report.line )


if __name__ == "__main__":
    sys.exit( main())

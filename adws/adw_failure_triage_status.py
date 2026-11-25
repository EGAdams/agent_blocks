#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Failure Triage Status -- read the triage ledger. Reads only; this is the Bridge's window.

    adw_failure_triage_status.py status                     live clusters, counts, the largest three
    adw_failure_triage_status.py clusters [--state open]    every cluster, or those in one state
    adw_failure_triage_status.py cluster --cluster 3f2a     one cluster in full (an id prefix of 4+)
"""
import argparse
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from failure_triage.adw import finish, new_id            # noqa: E402
from failure_triage.kit import build_desk                 # noqa: E402
from failure_triage.status import UnknownCluster          # noqa: E402


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description="Read the failure-triage ledger." )
    parser.add_argument( "operation", choices=[ "status", "clusters", "cluster" ])
    parser.add_argument( "--state", default="", choices=[ "", "open", "routed", "fixed", "closed" ])
    parser.add_argument( "--cluster", default="" )
    parser.add_argument( "--json", action="store_true" )
    parser.add_argument( "--json-out" )
    parser.add_argument( "--task-id" )               # the Bridge's own bookkeeping; not used here
    parser.add_argument( "--client" )
    args = parser.parse_args( argv )
    adw_id, desk = new_id(), build_desk()
    try:
        if args.operation == "status":
            line, data = desk.status()
        elif args.operation == "clusters":
            line, data = desk.clusters( args.state )
        else:
            line, data = desk.cluster( args.cluster )
    except ( UnknownCluster, ValueError ) as exc:
        return finish( args.json_out, False, f"REFUSED: { exc }", adw_id=adw_id, as_json=args.json )
    return finish( args.json_out, True, line, data, adw_id, as_json=args.json )


if __name__ == "__main__":
    sys.exit( main())

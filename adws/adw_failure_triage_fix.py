#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Failure Triage Fix -- record that a cluster was fixed, or send it to its owner again.

    adw_failure_triage_fix.py fix     --cluster 3f2a --fix "7fa5717 Machine Ops: unit no longer required"
    adw_failure_triage_fix.py reroute --cluster 3f2a

`fix` marks the cluster fixed (it closes after a quiet spell, and reopens as a regression if it fails
again) and writes the correction into the mechanic's case history, so her next diagnosis of the same error
says what fixed it. It changes the ledger only; it never touches a failed envelope.
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
    parser = argparse.ArgumentParser( description="Record a fix, or re-route a cluster." )
    parser.add_argument( "operation", nargs="?", default="fix", choices=[ "fix", "reroute" ])
    parser.add_argument( "--cluster", required=True )
    parser.add_argument( "--fix", default="" )
    parser.add_argument( "--json", action="store_true" )
    parser.add_argument( "--json-out" )
    parser.add_argument( "--task-id" )
    parser.add_argument( "--client" )
    args = parser.parse_args( argv )
    adw_id, desk = new_id(), build_desk()
    try:
        line, data = desk.record_fix( args.cluster, args.fix ) if args.operation == "fix" else desk.reroute( args.cluster )
    except ( UnknownCluster, ValueError ) as exc:
        return finish( args.json_out, False, f"REFUSED: { exc }", adw_id=adw_id, as_json=args.json )
    return finish( args.json_out, True, line, data, adw_id, as_json=args.json )


if __name__ == "__main__":
    sys.exit( main())

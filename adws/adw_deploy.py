#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic"]
# ///
"""ADW Deploy -- one traceable deployment operation per run, over the projects in deployment/projects.json.

    uv run adws/adw_deploy.py deploy.projects
    uv run adws/adw_deploy.py deploy.status   --project dashboard
    uv run adws/adw_deploy.py deploy.health   --project agent_blocks
    uv run adws/adw_deploy.py deploy.logs     --project dashboard --unit dashboard-server.log --lines 100
    uv run adws/adw_deploy.py deploy.history  --project dashboard
    uv run adws/adw_deploy.py deploy.deploy   --project dashboard --reason "Notes: active warning blinks" [--dry-run]
    uv run adws/adw_deploy.py deploy.restart  --project agent_blocks [--unit toyota-receptionist.service]
    uv run adws/adw_deploy.py deploy.rollback --project dashboard --reason "blink broke the preview" [--release-id ID]

Every run gets an ADW id (printed first, as `adw_id: xxxxxxxx`), a run record with every step it took
(~/.local/share/adw_deploy/runs/<adw_id>.json) and one ledger line (ledger.jsonl). `--json-out` writes the
outcome: {ok, verified, line, data}. A project, its commands, its services and its health checks come only
from projects.json; a request names a project and an operation, never a path or a command.
Read deployment/DESIGN.md first.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from deployment.kit import build_runner                     # noqa: E402
from deployment.models import OPERATIONS, DeployRequest     # noqa: E402


def main( argv: list[ str ]) -> int:
    names = [ f"deploy.{ op }" for op in OPERATIONS ]
    parser = argparse.ArgumentParser( prog="adw_deploy.py", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "operation", choices=names )
    parser.add_argument( "--project", default="" )
    parser.add_argument( "--reason", default="" )
    parser.add_argument( "--release-id", "--release_id", dest="release_id", default="" )
    parser.add_argument( "--unit", default="" )
    parser.add_argument( "--lines", type=int, default=80 )
    parser.add_argument( "--dry-run", "--dry_run", dest="dry_run", action="store_true" )
    parser.add_argument( "--task-id", "--task_id", dest="task_id", default="" )
    parser.add_argument( "--client", default="" )
    parser.add_argument( "--json-out", "--json_out", dest="json_out", default="" )
    args = parser.parse_args( argv )

    adw_id = uuid.uuid4().hex[ :8 ]
    print( f"adw_id: { adw_id }", flush=True )
    request = DeployRequest( operation=args.operation.split( ".", 1 )[ 1 ], project=args.project, reason=args.reason,
                             release_id=args.release_id, unit=args.unit, lines=args.lines, dry_run=args.dry_run,
                             task_id=args.task_id, client=args.client )
    outcome = build_runner( adw_id=adw_id ).run( request )
    print( outcome.line )
    if args.json_out:
        Path( args.json_out ).write_text( outcome.model_dump_json( indent=1 ), encoding="utf-8" )
    else:
        print( json.dumps( outcome.data, indent=1, default=str )[ :20000 ])
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))

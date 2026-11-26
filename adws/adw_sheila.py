#!/usr/bin/env python3
"""ADW Sheila -- run one of Sheila's deterministic operations here, the way her executor runs it. No model.

    lancedb_memory/.venv/bin/python adws/adw_sheila.py status            her source-of-truth snapshot, as JSON
    lancedb_memory/.venv/bin/python adws/adw_sheila.py status --check    one line; exit 1 unless she is ready
    lancedb_memory/.venv/bin/python adws/adw_sheila.py bridge            gateway health and agent discovery
    lancedb_memory/.venv/bin/python adws/adw_sheila.py run_adw --adw machine_watch_dry_run
    lancedb_memory/.venv/bin/python adws/adw_sheila.py message --to hello-world --task-type hello --text ping

Same card, same operations, same ledger as a Bridge or Post Office task: it builds her executor from
`agents.json` through the post office factory and hands it one envelope. `--check` is what the Machine
Supervisor's `sheila` check on BATI runs. Exit status: 0 the operation succeeded (and, with --check, she is
ready), 1 not. Pi is never started by this ADW: only a free-text task does that.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE.parent / "lancedb_memory" ))

from agent_comms.executors.sheila_ops import body_of  # noqa: E402
from agent_comms.factory import build_post_office  # noqa: E402
from agent_comms.models import Envelope, MessageKind  # noqa: E402

OPERATIONS = ( "status", "bridge", "run_adw", "message" )


def request_for( args: argparse.Namespace ) -> dict:
    request: dict = { "sheila": args.operation }
    if args.operation == "run_adw":
        request[ "adw" ] = args.adw
    if args.operation == "message":
        request.update({ "to": args.to, "task_type": args.task_type, "text": args.text })
    return request


def main() -> int:
    parser = argparse.ArgumentParser( description="Run one of Sheila's operations here." )
    parser.add_argument( "operation", choices=OPERATIONS )
    parser.add_argument( "--adw", default="" )
    parser.add_argument( "--to", default="hello-world" )
    parser.add_argument( "--task-type", default="hello" )
    parser.add_argument( "--text", default="ping from adw_sheila" )
    parser.add_argument( "--check", action="store_true", help="status only: one line, exit 1 unless ready" )
    parser.add_argument( "--sender", default="claude-code", help="who is asking (recorded in her ledger)" )
    args = parser.parse_args()
    adw_id = uuid.uuid4().hex[ :8 ]
    if not args.check:
        print( f"adw_id: { adw_id }", flush=True )

    kit  = build_post_office( forward_failures_to_mechanic=False )
    card = kit.registry.card( "sheila" )
    if card is None:
        print( "sheila: no card in agents.json" ); return 1
    executor = kit.executors.executor_for( card )
    stamp    = datetime.now( timezone.utc ).strftime( "%Y%m%dT%H%M%SZ" )
    envelope = Envelope( id=f"{ stamp }_{ adw_id }_from_{ args.sender }", kind=MessageKind.REQUEST, sender=args.sender,
                         recipient="sheila", task_type="inspect", text=json.dumps( request_for( args )),
                         correlation_id=f"adw_sheila_{ adw_id }", context={ "adw_id": adw_id })
    result = asyncio.run( executor.execute( envelope ))

    if args.check:
        if args.operation != "status" or not result.ok:
            print( f"sheila: { result.text[ :300 ]}" ); return 1
        snap = body_of( result.text )
        if snap[ "runtime_state" ] != "ready":
            print( f"sheila on { snap[ 'machine' ]}: { snap[ 'runtime_state' ]}; " + "; ".join( snap[ "blockers" ])); return 1
        print( f"sheila on { snap[ 'machine' ]}: ready (pi { snap[ 'facts' ][ 'pi' ].get( 'version' )}, "
               f"login { snap[ 'facts' ].get( 'login', {}).get( 'days_left' )} days)" )
        return 0
    print( result.text )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit( main())

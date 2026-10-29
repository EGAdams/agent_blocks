#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Service -- Cashy's sidecar for the dashboard, on 127.0.0.1:8770.

The dashboard's Agent Management tab talks to her through the same bridge it
uses for Toyota and Sydney (POST /turn streams receptionist_team's NDJSON
TurnRecords). She carries on the same session as adw_cashy_chat.py, so a
conversation started in the terminal continues on the dashboard.

Normally run as the user unit `cashy-agent.service` (see
cashy/deploy/cashy-agent.service), not by hand.

    cd /home/adamsl/agent_blocks && uv run --quiet adws/adw_cashy_service.py

Phases: code(cashy_turn), served
"""
import argparse
import sys
from pathlib import Path

sys.path[ :0 ] = [ str( Path( __file__ ).resolve().parent ), str( Path( __file__ ).resolve().parents[ 1 ] / "lancedb_memory" )]

from cashy.kit import DATA_DIR, CashyKit                                    # noqa: E402
from cashy.observing.observers import JsonlCashyObserver                     # noqa: E402
from cashy.presenting.sidecar import DEFAULT_PORT, LOOPBACK, CashySidecar    # noqa: E402


def main( argv: list[ str ]) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--port", type=int, default=DEFAULT_PORT, help="her loopback port" )
    parser.add_argument( "--chrome-port", type=int, default=9222, help="the debug Chrome's DevTools port" )
    parser.add_argument( "--session", default="default", help="which conversation the dashboard carries on" )
    args = parser.parse_args( argv )

    kit    = CashyKit( port=args.chrome_port, observers=[ JsonlCashyObserver( DATA_DIR / "events.jsonl" )],
                       launch_chrome=True )
    server = CashySidecar( kit.desk( args.session ), args.port )
    print( f"Cashy service -- http://{ LOOPBACK }:{ args.port }  session: { args.session }  "
           f"debug Chrome port: { args.chrome_port }", flush=True )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Chat -- Talk to Cashy, the banking navigation agent, turn by turn.

She drives the debug Chrome (start it with adw_interface_browser_debug_open.py)
by reading its DOM over the DevTools protocol. You sign in yourself; she never
sees or keeps a password, an MFA code or a cookie. Type "help" for what she
understands, "quit" to leave. The session survives: run this again (or say
"resume") to carry on where you stopped.

    cd /home/adamsl/agent_blocks && uv run adws/adw_cashy_chat.py --port 9222

Phases: code(cashy_turn), repeated
"""
import argparse
import sys
from pathlib import Path

sys.path[ :0 ] = [ str( Path( __file__ ).resolve().parent ), str( Path( __file__ ).resolve().parents[ 1 ] / "lancedb_memory" )]

from cashy.kit import DATA_DIR, CashyKit                                    # noqa: E402
from cashy.observing.observers import ConsoleCashyObserver, JsonlCashyObserver   # noqa: E402

QUIT = { "quit", "exit", "bye", "q" }


def main( argv: list[ str ]) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--port", type=int, default=9222, help="the debug Chrome's DevTools port" )
    parser.add_argument( "--session", default="default", help="which conversation to carry on" )
    parser.add_argument( "--quiet", action="store_true", help="don't print what she is doing between replies" )
    parser.add_argument( "--say", default="status", help="the first thing to say (default: status)" )
    args = parser.parse_args( argv )

    watchers = [ JsonlCashyObserver( DATA_DIR / "events.jsonl" )] + ( [] if args.quiet else [ ConsoleCashyObserver()])
    agent    = CashyKit( port=args.port, observers=watchers ).agent()
    print( "cashy> " + agent.turn( args.say, session_id=args.session ).text.replace( "\n", "\n       " ))
    while True:
        try:
            text = input( "you> " ).strip()
        except ( EOFError, KeyboardInterrupt ):
            print()
            return 0
        if text.lower() in QUIT:
            return 0
        if text:
            print( "cashy> " + agent.turn( text, session_id=args.session ).text.replace( "\n", "\n       " ))


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))

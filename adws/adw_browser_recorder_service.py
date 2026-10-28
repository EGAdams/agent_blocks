#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "aiohttp"]
# ///
"""ADW Browser Recorder Service -- the local service the Chrome extension
reports to, and the one that writes recordings to SQLite.

Long-running, so not a phase: `adw_browser_start_recording.py` starts it
detached when it is not already up, and you seldom need to run it yourself.
It listens on 127.0.0.1 only (WSL's mirrored networking makes that the same
address Windows Chrome sees), takes the WebSocket only from this extension's
own origin, and refuses any control request a web page could make.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_recorder_service.py [--port 8781]

Logs: adws/adw_data/browser_workflows/logs/recorder.jsonl
"""
import argparse
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))

from aiohttp import web

from browser_workflows.kit import DEFAULT_PORT, WorkflowKit


def main( argv: list[ str ]) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--port", type=int, default=DEFAULT_PORT )
    port = parser.parse_args( argv ).port
    web.run_app( WorkflowKit( port=port ).app(), host="127.0.0.1", port=port, print=lambda line: print( line, flush=True ))
    return 0


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))

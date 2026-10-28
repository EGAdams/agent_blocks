#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Route Request -- Work out which agent a free-text request is for, and what kind of work it is. Sends nothing.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_route_request.py --sender <sender> --text <text> \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(route_request)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "sender", "who is asking", required=True ),
    Flag( "text", "the request in the user's words", required=True ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "route_request", __doc__, sys.argv[ 1: ], FLAGS ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Hello World -- Say Hello World -- the smallest ADW; proves the harness, a session and a phase end to end.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_hello_world.py  \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(hello_world)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS: list[ Flag ] = []

if __name__ == "__main__":
    sys.exit( run_operation( "hello_world", __doc__, sys.argv[ 1: ], FLAGS ))

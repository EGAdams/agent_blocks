#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Send Message -- Post a request (JSON) to its recipient's mailbox and wake a worker. Never waits.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_send_message.py --request <request> \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(send_message)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "request", "the TaskRequest as JSON", required=True, kind="json" ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "send_message", __doc__, sys.argv[ 1: ], FLAGS ))

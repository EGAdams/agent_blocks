#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Receive Message -- Take the next reply waiting in one agent's inbox.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_receive_message.py --agent <agent> \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(receive_message)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "agent", "whose inbox", required=True ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "receive_message", __doc__, sys.argv[ 1: ], FLAGS ))

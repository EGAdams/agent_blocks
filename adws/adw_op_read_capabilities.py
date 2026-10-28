#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Read Capabilities -- Read one agent's capabilities from its registry card.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_read_capabilities.py --agent <agent> \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(read_capabilities)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS = [
    Flag( "agent", "the agent's registered name", required=True ),
]

if __name__ == "__main__":
    sys.exit( run_operation( "read_capabilities", __doc__, sys.argv[ 1: ], FLAGS ))

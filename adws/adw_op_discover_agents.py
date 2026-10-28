#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Op Discover Agents -- List every agent registered with the post office.

One post-office operation as one ADW run (see post_office_adw.py and
lancedb_memory/agent_comms/README.md). Run under lancedb_memory's venv:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_discover_agents.py  \\
        [--message-out /tmp/line.txt] [--json-out /tmp/outcome.json] [--adw-id a1b2c3d4]

Phases: code(discover_agents)
"""
import sys

from post_office_adw import Flag, run_operation

FLAGS: list[ Flag ] = []

if __name__ == "__main__":
    sys.exit( run_operation( "discover_agents", __doc__, sys.argv[ 1: ], FLAGS ))

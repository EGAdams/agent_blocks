#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Remember -- Save how Cashy got here as a new candidate version (the old ones stay).

    cd /home/adamsl/agent_blocks && uv run adws/adw_cashy_remember.py --port 9222

One Cashy operation as one ADW run, in-process, over the DevTools protocol
(see cashy/DESIGN.md).

Phases: code(cashy_remember)
"""
import sys

from browser_workflow_adw import ENDPOINT_FLAGS, Flag, run_browser_operation
from cashy.operations import cashy_operations

FLAGS = [
    Flag( "session", "which conversation (default \"default\")" ),
    Flag( "actor", "who is asking: user (default), toyota, sydney, ... -- only the user may approve" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "cashy_remember", __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=cashy_operations, default_transport="cdp" ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Open Bank -- Open a bank's login page (its login ADW first, if one is learned), then wait for you to sign in yourself.

    cd /home/adamsl/agent_blocks && uv run adws/adw_cashy_open_bank.py --port 9222

One Cashy operation as one ADW run, in-process, over the DevTools protocol
(see cashy/DESIGN.md).

Phases: code(cashy_open_bank)
"""
import sys

from browser_workflow_adw import ENDPOINT_FLAGS, Flag, run_browser_operation
from cashy.operations import cashy_operations

FLAGS = [
    Flag( "bank", "the bank, e.g. Chase" ),
    Flag( "session", "which conversation (default \"default\")" ),
    Flag( "actor", "who is asking: user (default), toyota, sydney, ... -- only the user may approve" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "cashy_open_bank", __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=cashy_operations, default_transport="cdp" ))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Cashy Get Statement -- Download one month's statement: learned ADW first, exploring if none works. Never imports it into the books.

    cd /home/adamsl/agent_blocks && uv run adws/adw_cashy_get_statement.py --port 9222

One Cashy operation as one ADW run, in-process, over the DevTools protocol
(see cashy/DESIGN.md).

Phases: code(cashy_get_statement)
"""
import sys

from browser_workflow_adw import ENDPOINT_FLAGS, Flag, run_browser_operation
from cashy.operations import cashy_operations

FLAGS = [
    Flag( "bank", "the bank, e.g. Chase" ),
    Flag( "period", "the month and year, e.g. \"September 2025\"" ),
    Flag( "account", "the account, masked, e.g. •6285" ),
    Flag( "again", "download it even if it was downloaded before", kind="bool" ),
    Flag( "session", "which conversation (default \"default\")" ),
    Flag( "actor", "who is asking: user (default), toyota, sydney, ... -- only the user may approve" ),
    *ENDPOINT_FLAGS,
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "cashy_get_statement", __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=cashy_operations, default_transport="cdp" ))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW ChatGPT health -- is one ChatGPT session reachable, and is its Chrome up.

    cd /home/adamsl/agent_blocks && uv run adws/adw_chatgpt_health.py

`adw_chatgpt_relay.py relay.health` with the operation pinned. One of the deliberately simple ADWs: it
exists so every step of the relay has a command of its own while the relay is being debugged, and it is
among the first things to collapse back into the multi-operation ADW afterwards.

Phases: code(relay_health)
"""
import sys
from pathlib import Path

_HERE = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( _HERE ))

from adw_chatgpt_relay import FLAGS
from browser_workflow_adw import run_browser_operation
from chatgpt_relay.catalog import relay_operations

if __name__ == "__main__":
    sys.exit( run_browser_operation( "relay_health", __doc__, sys.argv[ 1: ], FLAGS,
                                     catalog=lambda kit, console: relay_operations(),
                                     default_transport="cdp" ))

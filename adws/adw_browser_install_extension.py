#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Browser Install Extension -- Copy the click recorder extension to
%LOCALAPPDATA%\\adw-browser-recorder so Windows Chrome can load it unpacked.

Chrome does not let a script install an extension; you load it once by hand
(the line this prints says where to click). Run it again after the extension
changes, then press Reload on its card in chrome://extensions.

    cd /home/adamsl/agent_blocks && uv run adws/adw_browser_install_extension.py

Phases: code(install_extension)
"""
import sys

from browser_workflow_adw import Flag, run_browser_operation

FLAGS = [
    Flag( "to", "a Windows folder instead of %LOCALAPPDATA%\\adw-browser-recorder" ),
]

if __name__ == "__main__":
    sys.exit( run_browser_operation( "install_extension", __doc__, sys.argv[ 1: ], FLAGS ))

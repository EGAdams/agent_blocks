#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic"]
# ///
"""Browser Automation Workspace -- the Smart Menu for recording and replaying
browser workflows.

    1. Choose Playback      (every saved playback, read fresh from SQLite)
    2. Start Recording      (the debug Chrome's selected tab)
    3. Stop Recording       (then "Save As?")
    4. Edit Saved Playback
    5. View History
    6. Exit this menu

Mounted in /home/adamsl/rol_finances/menu_code/rol_finances_menu.json; run
on its own with:

    cd /home/adamsl/agent_blocks && uv run adws/browser_workspace_menu.py

Every choice runs one `adw_browser_*.py` ADW on this terminal, so a playback
can ask you to approve a payment or type a password, and every run is in
`just obs` like any other ADW's. This file is the composition root: the only
place that names smart_menu's Menu and the concrete parts.
"""
import sys
from pathlib import Path

SMART_MENU = "/home/adamsl/smart_menu"
sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))
sys.path.insert( 0, SMART_MENU )

from Menu import Menu                                            # smart_menu

from browser_workflows.kit import WorkflowKit
from browser_workflows.presenting.adw_actions import AdwWorkspaceActions, SubprocessAdwRunner
from browser_workflows.presenting.menu_provider import RepositoryPlaybackMenu
from browser_workflows.presenting.terminal import TerminalConsole
from browser_workflows.presenting.workspace_menu import build_workspace_menu


def main() -> int:
    console = TerminalConsole()
    console.say( "Browser Automation Workspace (Chrome must be open with "
                 "adw_interface_browser_debug_open.py and the recorder extension loaded)\n" )
    menu = build_workspace_menu( RepositoryPlaybackMenu( WorkflowKit().repository()),
                                 AdwWorkspaceActions( SubprocessAdwRunner()), console, Menu )
    menu.display_and_select()
    return 0


if __name__ == "__main__":
    sys.exit( main())

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic"]
# ///
"""Cashy -- Banking Automation Workspace: the Smart Menu for Cashy.

    1. Start Interactive Session       6. Play Saved Recording
    2. Resume Interactive Session      7. View Learned Banking Procedures
    3. Execute Saved Banking ADW       8. Inspect / Edit Banking ADWs
    4. Explore a Banking Website       9. View Execution History
    5. Record Browser Workflow        10. Exit

Mounted in /home/adamsl/rol_finances/menu_code/rol_finances_menu.json; run
on its own with:

    cd /home/adamsl/agent_blocks && uv run adws/cashy_menu.py

Every choice runs an ADW on this terminal. This file is the composition
root: the only place that names smart_menu's Menu and the concrete parts.
"""
import subprocess
import sys
from pathlib import Path

SMART_MENU = "/home/adamsl/smart_menu"
ADWS       = Path( __file__ ).resolve().parent
sys.path.insert( 0, str( ADWS ))
sys.path.insert( 0, SMART_MENU )

from Menu import Menu                                            # smart_menu

from browser_workflows.kit import DEFAULT_DB_PATH, WorkflowKit
from browser_workflows.presenting.adw_actions import AdwWorkspaceActions, SubprocessAdwRunner
from browser_workflows.presenting.menu_provider import RepositoryPlaybackMenu
from browser_workflows.presenting.terminal import TerminalConsole
from browser_workflows.storage.database import SqliteDatabase
from cashy.presenting.menu import CashyMenuActions, build_cashy_menu
from cashy.storage.database import cashy_database
from cashy.storage.sanitizer import MaskingSanitizer
from cashy.storage.sessions import SqliteSessionStore
from cashy.storage.workflows import SqliteWorkflowRegistry


def _chat( args: list[ str ]) -> None:
    subprocess.run([ "uv", "run", "--quiet", str( ADWS / "adw_cashy_chat.py" ), *args ], cwd=ADWS.parent )


def main() -> int:
    console   = TerminalConsole()
    console.say( "Cashy -- Banking Automation Workspace (open the debug Chrome first: "
                 "adw_interface_browser_debug_open.py; you always sign in yourself)\n" )
    database  = cashy_database( SqliteDatabase( DEFAULT_DB_PATH ))
    workspace = AdwWorkspaceActions( SubprocessAdwRunner())
    actions   = CashyMenuActions( SubprocessAdwRunner( prefix="adw_cashy_" ), _chat, SubprocessAdwRunner())
    menu = build_cashy_menu( actions, workspace, RepositoryPlaybackMenu( WorkflowKit().repository()),
                             SqliteWorkflowRegistry( database ), SqliteSessionStore( database, MaskingSanitizer()),
                             console, Menu )
    menu.display_and_select()
    return 0


if __name__ == "__main__":
    sys.exit( main())

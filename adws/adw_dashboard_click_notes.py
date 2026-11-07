#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Dashboard Click Notes -- press the NOTES tab on the ROL dashboard, in
the Chrome window that is already open on the user's desktop.

The first proof that an ADW can drive the user's own browser: it attaches
selenium-headless's `WebBrowserManager` to the debug Chrome's DevTools port
(9222) and clicks one button. No Playwright, no new browser, no typing.

Nothing is reimplemented here. This is `adw_interface_browser_click.py` with
its target fixed to `dashboard_notes_tab` (an entry in
`browser_session/site_targets.py`), loaded by file location, so the seven
phases, tracing and wording are exactly that ADW's:

    request -> resolve_target -> probe -> attach -> select_tab -> locate -> click

The tab chosen is the open https tab on desktop-2obsqmc.tailb8fc54.ts.net.
The window is released afterwards, never closed: it is the user's.

Usage:
    uv run adws/adw_dashboard_click_notes.py ["<why>"] [--port 9222] [--dry-run]
        [--message-out /tmp/line.txt] [--adw-id a1b2c3d4]
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TARGET       = "dashboard_notes_tab"
DEFAULT_WHY  = "Open the NOTES tab on the dashboard"
CLICK_ADW    = Path( __file__ ).resolve().parent / "adw_interface_browser_click.py"


def _click_adw():
    """The general click ADW, imported by its file path (it is a script, not a package)."""
    spec   = importlib.util.spec_from_file_location( "adw_interface_browser_click", CLICK_ADW )
    module = importlib.util.module_from_spec( spec )
    spec.loader.exec_module( module )
    return module


def argv_for( argv: list[ str ]) -> list[ str ]:
    """The click ADW's argv: the reason first (a default if none was given),
    then the fixed target, then whatever flags the caller passed."""
    if "--target" in argv or any( a.startswith( "--target=" ) for a in argv ):
        raise SystemExit( "this ADW always clicks the NOTES tab; use adw_interface_browser_click.py for others" )
    why  = argv[ 0 ] if argv and not argv[ 0 ].startswith( "-" ) else DEFAULT_WHY
    rest = argv[ 1: ] if argv and not argv[ 0 ].startswith( "-" ) else argv
    return [ why, "--target", TARGET, *rest ]


if __name__ == "__main__":
    adws = str( CLICK_ADW.parent )
    if adws not in sys.path:
        sys.path.insert( 0, adws )
    sys.exit( _click_adw().build_application().run( argv_for( sys.argv[ 1: ])))

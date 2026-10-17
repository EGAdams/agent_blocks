#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""SSSF Debugging Console — run one phase at a time and look at what happened.

Usage:
    uv run adws/adw_debug_console.py [--config adws/adw_sssf_config/sssf.config.yaml]
                                     [--adw-id a1b2c3d4] [--smart-menu PATH]

Phases: open_file -> read_file -> print_file, each opened only when selected.

Not an ordinary ADW: an ADW's `main()` decides the order of its phases, this one
hands that decision to the engineer. Everything below the menu is the same
machinery — `session.ensure`, `run.phase`, the tracer, `events.jsonl`.

Three layers, on purpose:
    adw_modules/debug_steps.py    the work        (no SSSF, no menu)
    adw_modules/debug_console.py  the actions     (SSSF phases; returns objects)
    this file                     the terminal    (smart_menu; prints objects)
Only this file prints, so the action layer is reusable from an HTTP handler.
"""

import argparse
import os
import sys
from pathlib import Path

# Black and white, as asked. rich reads NO_COLOR when it builds a Console, and
# SSSF's own Console is built inside session.ensure() below — so this must be
# set before that import chain runs, not after.
os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")

from adw_modules import agents                                  # noqa: E402
from adw_modules.data_types import ActionRequest, ActionResult  # noqa: E402
from adw_modules.debug_console import DebugConsole              # noqa: E402

DEFAULT_SMART_MENU = "/home/adamsl/smart_menu"
SCOPE_LABEL = {"adw": "whole ADW", "phase": "phase", "inspect": "inspect",
               "session": "session"}
RULE = "-" * 72


def load_smart_menu(path: str):
    """Import the engineer's existing smart menu system, or say where it isn't.

    Its modules use flat imports (`from MenuItem import MenuItem`), so the
    directory itself has to be on sys.path — this is not a package.
    """
    root = Path(path).expanduser()
    if not (root / "Menu.py").is_file():
        raise SystemExit(f"smart menu system not found: no Menu.py under {root}\n"
                         f"pass --smart-menu PATH to point at your checkout.")
    sys.path.insert(0, str(root))
    from Menu import Menu
    from MenuItem import MenuItem
    return Menu, MenuItem


def build_callable_item(MenuItem):
    """The one adapter the smart menu needs: a menu item that calls Python.

    `MenuItem.execute` shells out, and `SmartMenuItem` only adds submenus —
    neither can hold in-process state, which is the whole point here. Adding a
    third subclass in this file reuses `Menu.display_and_select` verbatim
    (numbering, "Exit this menu", the invalid-selection message) and leaves
    every file under /home/adamsl/smart_menu untouched.
    """

    class CallableMenuItem(MenuItem):
        def __init__(self, title, handler):
            super().__init__(title, None, "", False, False)
            self.handler = handler

        def execute(self):
            self.handler()

    return CallableMenuItem


# ── rendering (the only printing in the whole feature) ──────────────────────

def show_preview(preview) -> None:
    spec = preview.spec
    print(f"\n{RULE}")
    print(f"ACTION : {spec.title}   [{SCOPE_LABEL[spec.scope]}]")
    if spec.phase_name:
        print(f"PHASE  : {spec.phase_name}  (kind={spec.phase_kind}, owner={spec.owner})")
    if spec.scope == "adw":
        print("PHASES : open_file -> read_file -> print_file  (3 phases, one selection)")
    print(f"INTENT : {spec.description}")
    for key, value in preview.inputs.items():
        print(f"INPUT  : {key} = {value}")
    print(f"READY  : {'yes' if preview.ready else 'no'}")
    for item in preview.missing:
        print(f"         missing: {item}")
    print(RULE)


def show_result(result: ActionResult) -> None:
    print(f"OUTCOME: {result.outcome.upper()}")
    print(f"RESULT : {result.summary}")
    for line in result.detail:
        print(f"         {line}")
    if result.state_changed:
        print(f"CHANGED: {', '.join(result.state_changed)}")
    if result.error:
        print(f"ERROR  : {result.error}")
    print(f"TIMING : {result.elapsed_ms} ms")
    print(f"TRACE  : adw_id={result.adw_id}"
          f"{'  phase_id=' + result.phase_id if result.phase_id else ''}")
    print(f"LOGS   : {result.log_path}")
    print(f"{RULE}\n")


def make_handler(console: DebugConsole, action_id: str):
    """One menu selection: preview, run, report, return. Never auto-advances."""

    def handler() -> None:
        spec = next(s for s in console.specs() if s.action_id == action_id)
        path = ""
        if spec.needs_path:
            path = input("Path to the file: ").strip()
        request = ActionRequest(action_id=action_id, path=path)
        preview = console.preview(request)
        show_preview(preview)
        if not preview.ready:
            # Report and stop. Running the prerequisite ourselves would hide the
            # ordering the engineer is here to see.
            show_result(console.invoke(request))
            return
        show_result(console.invoke(request))

    return handler


def main(config: str, adw_id: str | None, smart_menu: str) -> int:
    Menu, MenuItem = load_smart_menu(smart_menu)
    CallableMenuItem = build_callable_item(MenuItem)

    cfg = agents.load_config(config)
    console = DebugConsole(cfg, adw_id)

    print(f"\n{RULE}")
    print("SSSF Debugging Console")
    print(f"session adw_id : {console.adw_id}")
    print(f"trace db       : {cfg.observability.db}")
    print("one selection runs one operation, then you are back here")
    print(f"{RULE}\n")

    menu = Menu()
    for spec in console.specs():
        label = f"{spec.title:<42} [{SCOPE_LABEL[spec.scope]}"
        label += f": {spec.phase_name}]" if spec.phase_name else "]"
        menu.add_item(CallableMenuItem(label, make_handler(console, spec.action_id)))

    try:
        menu.display_and_select()           # smart_menu's own loop and input handling
    except (KeyboardInterrupt, EOFError):
        print("\ninterrupted")
    finally:
        code = console.close()              # releases the handle, settles the run
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    parser.add_argument("--smart-menu", default=DEFAULT_SMART_MENU,
                        help=f"the smart menu system checkout (default {DEFAULT_SMART_MENU})")
    args = parser.parse_args()
    sys.exit(main(args.config, args.adw_id, args.smart_menu))

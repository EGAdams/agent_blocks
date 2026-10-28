"""The Browser Automation Workspace menu, as smart_menu items.

Each item is a Command (a title and `execute`) with one job; the menus
themselves are smart_menu `Menu`s, made by an injected factory so this
module never imports smart_menu. "Choose Playback" and the edit picker
build their submenu when chosen, from the database, so the list is always
current -- a recording saved by "Stop Recording" is there on the next pick.

    1. Choose Playback       5. View History
    2. Start Recording       6. Exit this menu   (smart_menu's own)
    3. Stop Recording        -- asks "Save As?" and saves, or discards
    4. Edit Saved Playback
"""
from __future__ import annotations

import json
from typing import Callable

from ..interfaces.presenting import IConsole, IMenu, IMenuItem, IPlaybackMenuProvider, IWorkspaceActions

MenuFactory = Callable[[ list[ IMenuItem ]], IMenu ]


def _report( console: IConsole, result: tuple[ bool, str ]) -> None:
    ok, line = result
    console.say( ( "" if ok else "Not done: " ) + line )


class ActionItem:
    """One workspace action, reported on the console."""

    def __init__( self, title: str, action: Callable[[], tuple[ bool, str ]], console: IConsole ) -> None:
        self.title   = title
        self._action = action
        self._console = console

    def execute( self ) -> None:
        _report( self._console, self._action())


class StopRecordingItem:
    """Stop, then "Save As?": a name saves it; an empty answer asks whether to throw it away."""
    title = "Stop Recording"

    def __init__( self, actions: IWorkspaceActions, console: IConsole ) -> None:
        self._actions = actions
        self._console = console

    def execute( self ) -> None:
        stopped, line = self._actions.stop_recording()
        self._console.say( line )
        if not stopped:
            return
        while True:
            name = self._console.ask( "Save As? Playback name (blank to discard): " ).strip()
            if not name:
                if self._console.ask( "Discard this recording? type YES: " ).strip() == "YES":
                    _report( self._console, self._actions.discard_recording())
                    return
                continue
            saved, line = self._actions.save_recording( name )
            _report( self._console, ( saved, line ))
            if saved:
                return


class PickPlaybackItem:
    """Opens a fresh submenu of every saved playback; choosing one hands its
    name to `then`."""

    def __init__( self, title: str, playbacks: IPlaybackMenuProvider, menus: MenuFactory, console: IConsole,
                  then: Callable[[ str ], None ]) -> None:
        self.title      = title
        self._playbacks = playbacks
        self._menus     = menus
        self._console   = console
        self._then      = then

    def execute( self ) -> None:
        entries = self._playbacks.entries()
        if not entries:
            self._console.say( "No saved playbacks yet. Use Start Recording, then Stop Recording to save one." )
            return
        self._menus([ _Chosen( e.title, e.name, self._then ) for e in entries ]).display_and_select()


class _Chosen:
    def __init__( self, title: str, name: str, then: Callable[[ str ], None ]) -> None:
        self.title, self._name, self._then = title, name, then

    def execute( self ) -> None:
        self._then( self._name )


class EditMenu:
    """The edits for one chosen playback, as a submenu. Each asks only what
    its edit needs, then runs edit_playback."""

    def __init__( self, actions: IWorkspaceActions, menus: MenuFactory, console: IConsole ) -> None:
        self._actions = actions
        self._menus   = menus
        self._console = console

    def open( self, name: str ) -> None:
        ask, say = self._console.ask, self._console.say
        state    = { "name": name }

        def edit( kind: str, **options: str ) -> None:
            ok, line = self._actions.edit_playback( state[ "name" ], kind, options )
            _report( self._console, ( ok, line.splitlines()[ 0 ] if line else line ))
            if ok and kind == "rename":
                state[ "name" ] = options[ "new_name" ]

        def show() -> None:
            _report( self._console, self._actions.describe_playback( state[ "name" ]))

        def delete() -> None:
            if ask( f"Delete '{ state[ 'name' ] }' for good? type YES: " ).strip() == "YES":
                edit( "delete", yes="" )
            else:
                say( "kept" )

        def waits() -> None:
            kind = ask( "Wait for (url_contains, url_equals, text_present, navigation_complete, element_visible): " ).strip()
            value = ask( "Value (URL part, text or CSS; blank for none): " ).strip()
            seconds = ask( "Timeout seconds [15]: " ).strip() or "15"
            conditions = [] if not kind else [{ "kind": kind, "value": value, "timeout_seconds": float( seconds )}]
            edit( "waits", position=ask( "Step number: " ).strip(), waits=json.dumps( conditions ))

        def insert() -> None:
            kind = ask( "Step type (click, enter_text, navigate, wait, press_key): " ).strip()
            action: dict = { "action_type": kind }
            if kind == "navigate":
                action[ "url" ] = ask( "URL: " ).strip()
            else:
                css = ask( "CSS selector (blank for none): " ).strip()
                if css:
                    action[ "locators" ] = [{ "strategy": "css", "value": css, "source": "edited", "rank": 20 }]
                if kind in ( "enter_text", "press_key" ):
                    action[ "value" ] = ask( "Text / key: " )
            edit( "insert", position=ask( "Insert before step number: " ).strip(), action=json.dumps( action ))

        items = [
            _Do( "Show steps", show ),
            _Do( "Rename", lambda: edit( "rename", new_name=ask( "New name: " ).strip())),
            _Do( "Duplicate", lambda: edit( "duplicate", new_name=ask( "Name for the copy: " ).strip())),
            _Do( "Move a step", lambda: edit( "move", position=ask( "Step number: " ).strip(), to=ask( "Move to: " ).strip())),
            _Do( "Remove a step", lambda: edit( "remove", position=ask( "Step number: " ).strip())),
            _Do( "Insert a step", insert ),
            _Do( "Change a step's selector", lambda: edit( "locators", position=ask( "Step number: " ).strip(),
                                                           css=ask( "New CSS selector: " ).strip())),
            _Do( "Change a step's text value", lambda: edit( "value", position=ask( "Step number: " ).strip(),
                                                              value=ask( "New text (not for secret fields): " ))),
            _Do( "Change a step's wait", waits ),
            _Do( "Require approval for a step", lambda: edit( "approval", position=ask( "Step number: " ).strip(),
                                                              required=ask( "Require approval? yes/no: " ).strip() or "yes" )),
            _Do( "Delete this playback", delete ),
        ]
        say( f"Editing '{ name }'" )
        self._menus( items ).display_and_select()


class _Do:
    def __init__( self, title: str, call: Callable[[], None ]) -> None:
        self.title, self._call = title, call

    def execute( self ) -> None:
        self._call()


class HistoryItem:
    title = "View History"

    def __init__( self, actions: IWorkspaceActions, console: IConsole ) -> None:
        self._actions = actions
        self._console = console

    def execute( self ) -> None:
        _report( self._console, self._actions.playback_history())
        run_id = self._console.ask( "Run id for its step-by-step detail (blank to go back): " ).strip()
        if run_id.isdigit():
            _report( self._console, self._actions.playback_history( run_id=int( run_id )))


def build_workspace_menu( playbacks: IPlaybackMenuProvider, actions: IWorkspaceActions, console: IConsole,
                          menus: MenuFactory ) -> IMenu:
    editor = EditMenu( actions, menus, console )
    return menus([
        PickPlaybackItem( "Choose Playback", playbacks, menus, console,
                          lambda name: _report( console, actions.execute_playback( name ))),
        ActionItem( "Start Recording", actions.start_recording, console ),
        StopRecordingItem( actions, console ),
        PickPlaybackItem( "Edit Saved Playback", playbacks, menus, console, editor.open ),
        HistoryItem( actions, console ),
    ])

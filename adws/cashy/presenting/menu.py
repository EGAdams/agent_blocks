"""The "Cashy -- Banking Automation Workspace" menu, as smart_menu items.

Every item runs an ADW on this terminal -- an `adw_cashy_*.py` or one of the
workspace's own `adw_browser_*.py` -- so the menu, Toyota, Sydney and Cashy
herself all reach the same workflows, and every run shows in `just obs`.
Recording, playing and editing are the Browser Automation Workspace's own
items, reused, not copied.

     1. Start Interactive Session       6. Play Saved Recording
     2. Resume Interactive Session      7. View Learned Banking Procedures
     3. Execute Saved Banking ADW       8. Inspect / Edit Banking ADWs
     4. Explore a Banking Website       9. View Execution History
     5. Record Browser Workflow        10. Exit   (smart_menu's own)
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from browser_workflows.interfaces.presenting import (
    IAdwRunner, IConsole, IMenu, IMenuItem, IPlaybackMenuProvider, IWorkspaceActions, PlaybackMenuEntry )
from browser_workflows.presenting.workspace_menu import (
    ActionItem, EditMenu, HistoryItem, MenuFactory, PickPlaybackItem, StopRecordingItem )

from ..interfaces.learning import IWorkflowRegistry
from ..interfaces.session import ISessionStore


def _report( console: IConsole, result: tuple[ bool, str ]) -> None:
    ok, line = result
    console.say( ( "" if ok else "Not done: " ) + line )


class Do:
    """One titled call."""

    def __init__( self, title: str, call: Callable[[], None ]) -> None:
        self.title, self._call = title, call

    def execute( self ) -> None:
        self._call()


class BankingPlaybacks:
    """IPlaybackMenuProvider: only the playbacks Cashy learned or a statement
    source names, out of everything the workspace holds."""

    def __init__( self, inner: IPlaybackMenuProvider, banking: Callable[[], set[ str ]]) -> None:
        self._inner   = inner
        self._banking = banking

    def entries( self ) -> list[ PlaybackMenuEntry ]:
        wanted = self._banking()
        return [ e for e in self._inner.entries() if e.name in wanted or e.name.startswith( "cashy_" )]


class CashyMenuActions:
    """What the Cashy-only items run, each one ADW."""

    def __init__( self, cashy: IAdwRunner, chat: Callable[[ list[ str ]], None ], workspace: IAdwRunner ) -> None:
        self._cashy     = cashy
        self._chat      = chat
        self._workspace = workspace

    def chat( self, session: str, first: str = "status" ) -> None:
        self._chat([ "--session", session, "--say", first ])

    def run_learned( self, playback: str, period: str ) -> tuple[ bool, str ]:
        args = [ "--name", playback, "--interactive", "--transport", "cdp" ] + ([ "--period", period ] if period else [])
        return self._workspace.run( "execute_saved_playback", args )

    def procedures( self ) -> tuple[ bool, str ]:
        ok, versions = self._cashy.run( "list_workflows", [])
        _, memories  = self._cashy.run( "search_memories", [ "--text", "statement activity login" ])
        return ok, f"{ versions }\n\nWhat I remember:\n{ memories }"

    def downloads( self ) -> tuple[ bool, str ]:
        return self._cashy.run( "download_history", [])


def build_cashy_menu( actions: CashyMenuActions, workspace: IWorkspaceActions, playbacks: IPlaybackMenuProvider,
                      registry: IWorkflowRegistry, sessions: ISessionStore, console: IConsole,
                      menus: MenuFactory ) -> IMenu:
    ask = console.ask

    def start() -> None:
        actions.chat( datetime.now().strftime( "session_%Y%m%d_%H%M%S" ))

    def resume() -> None:
        ids = sessions.ids()
        if not ids:
            console.say( "No sessions yet; start one." )
            return
        menus([ Do( i, lambda i=i: actions.chat( i, "resume" )) for i in ids ]).display_and_select()

    def run_learned() -> None:
        versions = [ v for v in registry.versions() if v.status != "retired" ]
        if not versions:
            console.say( "Cashy hasn't learned a banking workflow yet. Explore one, then say 'remember how you did that'." )
            return

        def go( playback: str, needs: list[ str ]) -> None:
            period = ask( "Which month and year? (e.g. October 2025): " ).strip() if needs else ""
            _report( console, actions.run_learned( playback, period ))

        menus([ Do( f"{ v.bank } / { v.goal } v{ v.version } ({ v.status })", lambda v=v: go( v.playback, v.needs ))
                for v in versions ]).display_and_select()

    def explore() -> None:
        bank = ask( "Which bank? (Chase, Fifth Third, Bank of America): " ).strip()
        if bank:
            actions.chat( datetime.now().strftime( "explore_%Y%m%d_%H%M%S" ), f"open { bank }" )

    def record() -> None:
        menus([ ActionItem( "Start Recording", workspace.start_recording, console ),
                StopRecordingItem( workspace, console )]).display_and_select()

    def history() -> None:
        _report( console, actions.downloads())
        HistoryItem( workspace, console ).execute()

    editor = EditMenu( workspace, menus, console )
    items: list[ IMenuItem ] = [
        Do( "Start Interactive Session", start ),
        Do( "Resume Interactive Session", resume ),
        Do( "Execute Saved Banking ADW", run_learned ),
        Do( "Explore a Banking Website", explore ),
        Do( "Record Browser Workflow", record ),
        PickPlaybackItem( "Play Saved Recording", playbacks, menus, console,
                          lambda name: _report( console, workspace.execute_playback( name ))),
        Do( "View Learned Banking Procedures", lambda: _report( console, actions.procedures())),
        PickPlaybackItem( "Inspect / Edit Banking ADWs", BankingPlaybacks( playbacks, lambda: {
            v.playback for v in registry.versions()}), menus, console, editor.open ),
        Do( "View Execution History", history ),
    ]
    return menus( items )

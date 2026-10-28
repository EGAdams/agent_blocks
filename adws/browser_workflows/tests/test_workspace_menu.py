"""The workspace menu, driven by a script of picks against fake actions.

Pinned: Choose Playback is read fresh each time (a playback saved by Stop
Recording is there on the next pick); Stop asks "Save As?" and an empty name
only discards after a YES; deleting asks first; and the ADW-backed actions
pass the right flags, playback always --interactive.
"""
from __future__ import annotations

from browser_workflows.interfaces.presenting import PlaybackMenuEntry
from browser_workflows.presenting.adw_actions import AdwWorkspaceActions
from browser_workflows.presenting.workspace_menu import build_workspace_menu

from .fakes import ScriptedConsole


class ScriptedMenus:
    """A menu factory whose menus pick from one shared script of numbers;
    the number after the last item means Exit, as in smart_menu."""

    def __init__( self, picks: list[ int ]) -> None:
        self.picks = picks
        self.shown: list[ list[ str ]] = []

    def __call__( self, items ):
        outer = self

        class _Menu:
            def display_and_select( self ) -> None:
                outer.shown.append([ i.title for i in items ])
                while outer.picks:
                    pick = outer.picks.pop( 0 )
                    if pick == len( items ) + 1:
                        return
                    items[ pick - 1 ].execute()
        return _Menu()


class FakeActions:
    def __init__( self ) -> None:
        self.calls: list[ tuple ] = []
        self.saved: list[ str ] = []

    def _did( self, *call ) -> tuple[ bool, str ]:
        self.calls.append( call )
        return True, f"{ call[ 0 ] } done"

    def start_recording( self ):
        return self._did( "start" )

    def stop_recording( self ):
        return self._did( "stop" )

    def save_recording( self, name ):
        self.saved.append( name )
        return self._did( "save", name )

    def discard_recording( self ):
        return self._did( "discard" )

    def execute_playback( self, name ):
        return self._did( "play", name )

    def describe_playback( self, name ):
        return self._did( "describe", name )

    def edit_playback( self, name, edit, options ):
        return self._did( "edit", name, edit, options )

    def playback_history( self, name="", run_id=0 ):
        return self._did( "history", run_id )


class SavedPlaybacks:
    def __init__( self, actions: FakeActions ) -> None:
        self._actions = actions

    def entries( self ):
        return [ PlaybackMenuEntry( title=n, recording_id=i, name=n ) for i, n in enumerate( self._actions.saved, 1 )]


def test_record_save_then_choose_it_from_a_fresh_list():
    actions = FakeActions()
    console = ScriptedConsole( answers=[ "Pay Rent" ])
    # Start, Stop (Save As "Pay Rent"), Choose Playback -> 1st -> Exit, Exit
    menus = ScriptedMenus([ 2, 3, 1, 1, 2, 6 ])
    build_workspace_menu( SavedPlaybacks( actions ), actions, console, menus ).display_and_select()
    assert actions.calls == [( "start", ), ( "stop", ), ( "save", "Pay Rent" ), ( "play", "Pay Rent" )]
    assert menus.shown[ 0 ] == [ "Choose Playback", "Start Recording", "Stop Recording", "Edit Saved Playback", "View History" ]
    assert menus.shown[ 1 ] == [ "Pay Rent" ]


def test_blank_name_discards_only_after_yes():
    actions = FakeActions()
    console = ScriptedConsole( answers=[ "", "no", "", "YES" ])
    build_workspace_menu( SavedPlaybacks( actions ), actions, console, ScriptedMenus([ 3, 6 ])).display_and_select()
    assert actions.calls == [( "stop", ), ( "discard", )]


def test_edit_rename_then_delete_asks_first():
    actions = FakeActions()
    actions.saved.append( "Pay Rent" )
    console = ScriptedConsole( answers=[ "Pay May Rent", "no", "YES" ])
    # Edit Saved Playback -> Pay Rent -> Rename, Delete (no), Delete (YES), Exit edit, Exit picker, Exit
    menus = ScriptedMenus([ 4, 1, 2, 11, 11, 12, 2, 6 ])
    build_workspace_menu( SavedPlaybacks( actions ), actions, console, menus ).display_and_select()
    edits = [ c for c in actions.calls if c[ 0 ] == "edit" ]
    assert edits == [( "edit", "Pay Rent", "rename", { "new_name": "Pay May Rent" }),
                     ( "edit", "Pay May Rent", "delete", { "yes": "" })]


def test_no_playbacks_says_how_to_make_one():
    actions = FakeActions()
    console = ScriptedConsole()
    build_workspace_menu( SavedPlaybacks( actions ), actions, console, ScriptedMenus([ 1, 6 ])).display_and_select()
    assert any( "Start Recording" in line for line in console.said )


class RecordingRunner:
    def __init__( self ) -> None:
        self.runs: list[ tuple[ str, list[ str ]]] = []

    def run( self, adw, args ):
        self.runs.append(( adw, args ))
        return True, "ok"


def test_adw_actions_pass_the_right_flags():
    runner  = RecordingRunner()
    actions = AdwWorkspaceActions( runner )
    actions.execute_playback( "Pay Rent" )
    actions.edit_playback( "Pay Rent", "delete", { "yes": "" })
    actions.edit_playback( "Pay Rent", "move", { "position": "2", "to": "4" })
    actions.playback_history( run_id=7 )
    assert runner.runs == [
        ( "execute_saved_playback", [ "--name", "Pay Rent", "--interactive" ]),
        ( "edit_playback", [ "--name", "Pay Rent", "--edit", "delete", "--yes" ]),
        ( "edit_playback", [ "--name", "Pay Rent", "--edit", "move", "--position", "2", "--to", "4" ]),
        ( "playback_history", [ "--run-id", "7" ]),
    ]

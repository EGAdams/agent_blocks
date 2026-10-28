"""The menu, the agents' entry point, and the service client they all use."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..models import ControllerStatus, RecordingSummary


class PlaybackMenuEntry( BaseModel ):
    title:        str
    recording_id: int
    name:         str


class IPlaybackMenuProvider( Protocol ):
    """The current playbacks as menu entries, read fresh every time."""

    def entries( self ) -> list[ PlaybackMenuEntry ]: ...


class IConsole( Protocol ):
    """Where a person reads and answers; a test answers from a script."""

    def say( self, text: str ) -> None: ...

    def ask( self, prompt: str ) -> str: ...

    def ask_secret( self, prompt: str ) -> str: ...


class IAdwRunner( Protocol ):
    """Runs one ADW on this terminal (so it can ask for approval or a secret)
    and returns whether it succeeded and the one line it wrote."""

    def run( self, adw: str, args: list[ str ]) -> tuple[ bool, str ]: ...


class IWorkspaceActions( Protocol ):
    """What the menu can ask for. Each one runs as its own ADW, so a menu
    choice is exactly as observable as an agent's or a script's."""

    def start_recording( self ) -> tuple[ bool, str ]: ...

    def stop_recording( self ) -> tuple[ bool, str ]: ...

    def save_recording( self, name: str ) -> tuple[ bool, str ]: ...

    def discard_recording( self ) -> tuple[ bool, str ]: ...

    def execute_playback( self, name: str ) -> tuple[ bool, str ]: ...

    def describe_playback( self, name: str ) -> tuple[ bool, str ]: ...

    def edit_playback( self, name: str, edit: str, options: dict[ str, str ]) -> tuple[ bool, str ]: ...

    def playback_history( self, name: str = "", run_id: int = 0 ) -> tuple[ bool, str ]: ...


class IMenuItem( Protocol ):
    """smart_menu's item shape: a title and a Command."""
    title: str

    def execute( self ) -> None: ...


class IMenu( Protocol ):
    """smart_menu's Menu: shows its items until the person picks Exit."""

    def display_and_select( self ) -> None: ...


class IRecorderClient( Protocol ):
    """The recorder service's control API, from a CLI, ADW or menu."""

    def status( self ) -> ControllerStatus | None:
        """None when no service answers."""
        ...

    def start( self ) -> ControllerStatus: ...

    def stop( self ) -> ControllerStatus: ...

    def save( self, name: str, description: str = "" ) -> ControllerStatus: ...

    def discard( self ) -> ControllerStatus: ...

    def capture( self, event: dict ) -> ControllerStatus: ...


class IServiceLauncher( Protocol ):
    """Makes sure the recorder service is up. Returns "" or why it is not."""

    def ensure_running( self ) -> str: ...


class IPlaybackMatcher( Protocol ):
    """Which saved playback a free-text request means: one, or a reason."""

    def match( self, text: str, playbacks: list[ RecordingSummary ]) -> tuple[ RecordingSummary | None, str ]: ...

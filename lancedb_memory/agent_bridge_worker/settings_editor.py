"""Change the poll settings: check, back up, write, restart the worker, confirm.

The dashboard's Poll Settings tab sends the numbers EG typed; this is what
decides whether they are allowed and puts them into effect. The limits are
`PollSettings`' own (idle never under 10 s, and so on), so the page can never
hold a rule the worker does not.

Each step is one method, so `adws/adw_bridge_poll_settings.py` can run them as
visible phases, and `PollSettingsEditor.apply()` runs them as plain code.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from .polling import PollSettings


class WorkerStatus( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    active:     str          = "unknown"     # systemd ActiveState
    pid:        int          = 0
    started_at: float | None = None          # unix seconds


class IWorkerService( Protocol ):
    """The worker as a service: is it running, and restart it."""

    def status( self ) -> WorkerStatus: ...

    def restart( self ) -> None: ...


class IPollStateReader( Protocol ):
    """What the running poll loop last said about itself (`poll_state.json`)."""

    def read( self ) -> dict[ str, Any ] | None: ...


class FieldProblem( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    field:   str          # a PollSettings key, or "" for the whole form
    message: str


class SettingsChange( BaseModel ):
    """The one answer the dashboard shows. No secrets; numbers and words only."""

    model_config = ConfigDict( extra="forbid" )

    ok:        bool
    message:   str
    problems:  list[ FieldProblem ] = []
    settings:  dict[ str, Any ] | None = None
    backup:    str = ""
    worker_pid: int = 0


class SettingsRejected( Exception ):
    def __init__( self, message: str, problems: list[ FieldProblem ] | None = None ) -> None:
        super().__init__( message )
        self.problems = problems or []


def _tidy( value: Any ) -> Any:
    """30.0 -> 30, so the file stays as readable as EG wrote it."""
    if isinstance( value, float ) and value.is_integer():
        return int( value )
    return value


class PollSettingsEditor:
    def __init__( self, path: Path, service: IWorkerService, state: IPollStateReader,
                  now: Callable[[], float ] = time.time, sleep: Callable[[ float ], None ] = time.sleep,
                  min_restart_gap: float = 15.0, confirm_seconds: float = 30.0 ) -> None:
        self._path, self._service, self._state = path, service, state
        self._now, self._sleep = now, sleep
        self._gap, self._confirm = min_restart_gap, confirm_seconds
        self.settings: PollSettings | None = None
        self.backup_path: Path | None = None
        self.worker_pid = 0
        self.running    = True      # False: the worker is stopped, so save only and leave it stopped

    # -- phases -----------------------------------------------------------
    def validate( self, raw: dict[ str, Any ]) -> PollSettings:
        try:
            self.settings = PollSettings.model_validate( raw )
        except ValidationError as error:
            problems = [ FieldProblem( field=str( e[ "loc" ][ 0 ]) if e[ "loc" ] else "",
                                       message=e[ "msg" ].removeprefix( "Value error, " ))
                         for e in error.errors()]
            raise SettingsRejected( "Those settings were not saved.", problems ) from None
        return self.settings

    def check_worker_free( self ) -> WorkerStatus:
        """Never restart in the middle of a task, nor twice in quick succession."""
        status = self._service.status()
        state  = self._state.read() or {}
        if status.active == "active" and state.get( "phase" ) == "working" and state.get( "pid" ) == status.pid:
            raise SettingsRejected( "The worker is running a ChatGPT task right now. Try again when it finishes." )
        if status.started_at and self._now() - status.started_at < self._gap:
            wait = int( self._gap - ( self._now() - status.started_at )) + 1
            raise SettingsRejected( f"The worker restarted a moment ago. Try again in { wait } s." )
        self.running = status.active == "active"
        return status

    def backup( self ) -> Path | None:
        if not self._path.exists():
            return None
        self.backup_path = self._path.with_name( self._path.name + ".bak" )
        self.backup_path.write_bytes( self._path.read_bytes())
        return self.backup_path

    def write( self ) -> dict[ str, Any ]:
        assert self.settings is not None, "validate() first"
        data = { key: _tidy( value ) for key, value in self.settings.model_dump( mode="json" ).items()}
        temp = self._path.with_suffix( ".tmp" )
        temp.write_text( json.dumps( data, indent=2 ) + "\n", encoding="utf-8" )
        temp.replace( self._path )
        return data

    def restart( self ) -> None:
        """Only a running worker is restarted: saving must never switch polling on."""
        if self.running:
            self._service.restart()

    def confirm( self ) -> int:
        """Wait for the new process to report its first check; its pid proves it read the new file."""
        if not self.running:
            return 0
        deadline = self._now() + self._confirm
        while self._now() < deadline:
            status = self._service.status()
            state  = self._state.read() or {}
            if ( status.active == "active" and status.pid and state.get( "pid" ) == status.pid
                 and state.get( "phase" ) in ( "waiting", "working" )):
                self.worker_pid = status.pid
                return status.pid
            self._sleep( 0.5 )
        raise SettingsRejected( "The settings were saved, but the worker has not reported a check since the "
                                "restart. Look at: journalctl --user -u agent-bridge-worker" )

    # -- the same thing as plain code ---------------------------------------
    def apply( self, raw: dict[ str, Any ]) -> SettingsChange:
        try:
            self.validate( raw )
            self.check_worker_free()
            self.backup()
            self.write()
            self.restart()
            self.confirm()
        except SettingsRejected as error:
            return self.rejected( error )
        return self.accepted()

    def rejected( self, error: SettingsRejected ) -> SettingsChange:
        return SettingsChange( ok=False, message=str( error ), problems=error.problems,
                               backup=str( self.backup_path or "" ))

    def accepted( self ) -> SettingsChange:
        data = { key: _tidy( value ) for key, value in self.settings.model_dump( mode="json" ).items()}
        message = ( "Saved. The worker restarted and is using the new settings." if self.running else
                    "Saved. The worker is stopped, so it was left stopped; it will use these when it starts." )
        return SettingsChange( ok=True, message=message,
                               settings=data, backup=str( self.backup_path or "" ), worker_pid=self.worker_pid )

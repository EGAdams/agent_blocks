#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Bridge Poll Settings -- change how often the ChatGPT bridge worker checks for tasks.

The dashboard's Poll Settings tab runs this when EG presses "Save and restart".
It reads the new settings as JSON on stdin (or --settings-file), checks them
against the worker's own `PollSettings` limits, backs up `poll_settings.json`
beside itself (`.bak`), writes it, restarts `agent-bridge-worker.service`, and
waits for the new process to report a check. It never contacts HostGator
itself; the restarted worker makes its usual first check.

It refuses, changing nothing, while the worker is running a task or when it
restarted under 15 s ago. A stopped worker is saved for, and left stopped.

    Phases:  ValidateSettings -> CheckWorkerFree -> BackupSettings
             -> WriteSettings -> RestartWorker -> ConfirmWorker

The last line on stdout is always one JSON `SettingsChange`. The logic is
`agent_bridge_worker.settings_editor.PollSettingsEditor`; `apply()` there is
the same run without the ADW.

    echo '{"idle_seconds": 45}' | lancedb_memory/.venv/bin/python adws/adw_bridge_poll_settings.py
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path( __file__ ).resolve().parent
for _path in ( _HERE, _HERE.parent / "lancedb_memory" ):
    if str( _path ) not in sys.path:
        sys.path.insert( 0, str( _path ))

from sssf_roles import ( ConsoleLifecycleLog, ILifecycleLog, IPhase, IPhaseHandle, LifecycleLogged,  # noqa: E402
                         ManualContextPhaseExecutor, PrintLifecycleLog, SessionRunFactory, SssfConfigLoader )

from adw_modules.data_types import PhaseParams  # noqa: E402

from agent_bridge_worker.settings_editor import PollSettingsEditor, SettingsChange, SettingsRejected  # noqa: E402

UNIT          = "agent-bridge-worker.service"
POLL_SETTINGS = _HERE.parent / "lancedb_memory" / "agent_bridge_worker" / "poll_settings.json"


class EditorPhase( LifecycleLogged, IPhase ):
    """One SSSF phase that calls one PollSettingsEditor method."""

    def __init__( self, name: str, description: str, act: Callable[[], Any ], logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._name, self._description, self._act = name, description, act

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="chatgpt-bridge", description=self._description )

    def act( self, handle: IPhaseHandle ) -> Any:
        return self._act()


def build_application( settings_path: Path ) -> PollSettingsEditor:
    """The composition root: the only place that names a concrete."""
    from agent_bridge_worker.adapters.poll_state_file import JsonPollStateFile
    from agent_bridge_worker.adapters.systemd_user_service import SystemdUserService

    return PollSettingsEditor( settings_path, SystemdUserService( UNIT ), JsonPollStateFile())


def phases( editor: PollSettingsEditor, raw: dict[ str, Any ]) -> list[ tuple[ str, str, Callable[[], Any ]]]:
    return [
        ( "validate_settings", "Check the numbers against the worker's own limits", lambda: editor.validate( raw )),
        ( "check_worker_free", "Refuse while a task runs or just after a restart", editor.check_worker_free ),
        ( "backup_settings",   "Copy poll_settings.json to poll_settings.json.bak", editor.backup ),
        ( "write_settings",    "Write the new poll_settings.json atomically", editor.write ),
        ( "restart_worker",    "Restart agent-bridge-worker so it reads them", editor.restart ),
        ( "confirm_worker",    "Wait for the new process to report its first check", editor.confirm ),
    ]


def read_request( args: argparse.Namespace ) -> dict[ str, Any ]:
    text = Path( args.settings_file ).read_text( encoding="utf-8" ) if args.settings_file else sys.stdin.read()
    data = json.loads( text or "{}" )
    if not isinstance( data, dict ):
        raise SettingsRejected( "Expected a JSON object of settings." )
    return data


def run( args: argparse.Namespace ) -> SettingsChange:
    editor = build_application( Path( args.poll_settings ))
    try:
        raw = read_request( args )
    except ( OSError, ValueError, SettingsRejected ) as error:
        return editor.rejected( error if isinstance( error, SettingsRejected ) else SettingsRejected( str( error )))
    boot: ILifecycleLog = PrintLifecycleLog()
    loader = SssfConfigLoader( boot )
    cfg    = loader.load( args.config )
    loader.validate( cfg, [])
    session  = SessionRunFactory( boot ).create( cfg, args.adw_id )
    log      = ConsoleLifecycleLog( session )
    executor = ManualContextPhaseExecutor( boot )
    try:
        for name, description, act in phases( editor, raw ):
            executor.execute( session, EditorPhase( name, description, act, log ))
    except SettingsRejected as error:
        session.finish( accepted=False, reason=str( error )[ :300 ])
        return editor.rejected( error )
    result = editor.accepted()
    session.finish( accepted=True, reason=result.message[ :300 ])
    return result


def main( argv: list[ str ] | None = None ) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--settings-file", default="", help="read the new settings from here instead of stdin" )
    parser.add_argument( "--poll-settings", default=str( POLL_SETTINGS ))
    parser.add_argument( "--config", default=str( _HERE / "adw_sssf_config" / "sssf.config.yaml" ))
    parser.add_argument( "--adw-id", default=None )
    args = parser.parse_args( argv )
    # Phase logging goes to stderr, so stdout carries only the one JSON answer.
    with contextlib.redirect_stdout( sys.stderr ):
        result = run( args )
    print( result.model_dump_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit( main())

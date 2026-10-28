#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW System Mechanic -- start, stop, restart, reload, enable or disable one
named systemd service (or just read its status), and report the before/after
state. Nothing else is on the menu: the only program this ADW ever invokes is
`systemctl` (optionally through `sudo -n` for a `--scope system` action), and
the verb it hands that program always comes from `ALLOWED_ACTIONS` below.
There is no code path anywhere in this file that opens a file for writing,
truncates one, or calls `os.remove` / `shutil.rmtree` / `unlink` -- a service
action cannot delete a file because deleting a file is not a thing `systemctl`
does, so the restriction is structural rather than a rule an agent could be
talked out of.

Five phases, all code -- no judgement call here needs an agent, so this ADW
names none in REQUIRED_AGENTS:

* `request` (engineer) -- record which service, which action, which scope.
* `validate` (code) -- the action must be in `ALLOWED_ACTIONS` and the service
  name must look like a systemd unit name. Anything else is refused before a
  single subprocess runs; this is the gate, not the honour system.
* `status_before` (code) -- `systemctl is-active` / `systemctl status` on the
  service, read before anything is touched.
* `execute` (code) -- the one phase that changes anything. `--dry-run` turns
  a mutating action (start/stop/restart/reload/reload-or-restart/try-restart/
  enable/disable) into a no-op that reports the command it would have run;
  a read-only action (status/is-active) always actually runs, because reading
  changes nothing to begin with.
* `status_after` (code) -- the same read, again, so "did it work" is answered
  by a second observation rather than assumed from a zero exit code.

Usage (from /home/adamsl/agent_blocks):
    uv run adws/adw_system_mechanic.py "<why this admin action is being run>" \\
        --service <unit> [--action restart] [--scope system|user] [--no-sudo] \\
        [--dry-run] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(validate) -> code(status_before) -> code(execute) -> code(status_after)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams
from adw_modules.runner import Run

REQUIRED_AGENTS: list[ str ] = []   # every phase here is a known command; no judgement call needed

# Actions that change the service's state. None of them is a file operation.
MUTATING_ACTIONS = frozenset({
    "start", "stop", "restart", "reload", "reload-or-restart", "try-restart", "enable", "disable",
})
# Actions that only read the service's state.
READONLY_ACTIONS = frozenset({ "status", "is-active" })
ALLOWED_ACTIONS = MUTATING_ACTIONS | READONLY_ACTIONS

# systemd unit names: letters, digits, and @ _ . : -  (an instance template's
# "@" and the usual ".service"/".timer" suffix both fall inside this).
SERVICE_NAME_RE = re.compile( r"^[A-Za-z0-9@_.:\-]+$" )

DEFAULT_TIMEOUT_SECONDS = 30


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class MechanicRequest:
    """Parsed command-line intent. Mirrors sssf_roles.PromptRequest's shape."""
    prompt:      str
    service:     str
    action:      str
    scope:       str          # "system" | "user"
    sudo:        bool
    dry_run:     bool
    message_out: str
    config:      str
    adw_id:      str | None


class ServiceStatus( BaseModel ):
    """One reading of a unit's state, from `systemctl is-active` / `status`."""
    service:   str
    scope:     str
    active:    str            # e.g. "active", "inactive", "failed", "unknown"
    detail:    str            # a short excerpt of `systemctl status`, for the record
    exit_code: int


class ActionResult( BaseModel ):
    """One systemctl invocation, or the dry-run stand-in for one."""
    command:   list[ str ]
    exit_code: int
    stdout:    str
    stderr:    str

    @property
    def ok( self ) -> bool:
        return self.exit_code == 0


# -- Roles ---------------------------------------------------------------

class IServiceStatusReader( ABC ):
    """Reads one unit's current state. Never mutates anything."""

    @abstractmethod
    def read( self, scope: str, service: str ) -> ServiceStatus: ...


class IServiceActionRunner( ABC ):
    """Runs (or, dry, describes) one systemctl verb against one unit."""

    @abstractmethod
    def run( self, scope: str, action: str, service: str, sudo: bool, dry_run: bool ) -> ActionResult: ...


# -- Adapters ------------------------------------------------------------

def _base_command( scope: str ) -> list[ str ]:
    return [ "systemctl", "--user" ] if scope == "user" else [ "systemctl" ]


class SystemctlStatusReader( LifecycleLogged, IServiceStatusReader ):
    """The only class allowed to know a status read is `systemctl is-active`
    followed by `systemctl status`. Both are read-only by definition."""

    def __init__( self, timeout_seconds: float, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._timeout = timeout_seconds

    def read( self, scope: str, service: str ) -> ServiceStatus:
        base = _base_command( scope )
        try:
            active_proc = subprocess.run( base + [ "is-active", service ], capture_output=True,
                                          text=True, timeout=self._timeout )
            active = ( active_proc.stdout or "" ).strip() or "unknown"
            detail_proc = subprocess.run( base + [ "status", "--no-pager", "-l", service ],
                                          capture_output=True, text=True, timeout=self._timeout )
            lines = ( detail_proc.stdout or detail_proc.stderr or "" ).splitlines()
            return ServiceStatus( service=service, scope=scope, active=active,
                                  detail="\n".join( lines[ :12 ]), exit_code=active_proc.returncode )
        except subprocess.TimeoutExpired:
            return ServiceStatus( service=service, scope=scope, active="unknown",
                                  detail=f"status read timed out after { self._timeout }s", exit_code=-1 )
        except OSError as error:
            return ServiceStatus( service=service, scope=scope, active="unknown",
                                  detail=f"could not run systemctl: { error }", exit_code=-1 )


class SystemctlActionRunner( LifecycleLogged, IServiceActionRunner ):
    """The only class allowed to know the command it builds is ever
    `[sudo -n] systemctl [--user] <action> <service>` -- nothing else, ever."""

    def __init__( self, timeout_seconds: float, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._timeout = timeout_seconds

    def run( self, scope: str, action: str, service: str, sudo: bool, dry_run: bool ) -> ActionResult:
        prefix = [ "sudo", "-n" ] if ( scope == "system" and sudo ) else []
        command = prefix + _base_command( scope ) + [ action, service ]
        if dry_run:
            return ActionResult( command=command, exit_code=0, stdout="(dry run -- not executed)", stderr="" )
        try:
            proc = subprocess.run( command, capture_output=True, text=True, timeout=self._timeout )
            return ActionResult( command=command, exit_code=proc.returncode,
                                 stdout=( proc.stdout or "" ).strip(), stderr=( proc.stderr or "" ).strip())
        except subprocess.TimeoutExpired:
            return ActionResult( command=command, exit_code=-1, stdout="",
                                 stderr=f"timed out after { self._timeout }s" )
        except OSError as error:
            return ActionResult( command=command, exit_code=-1, stdout="", stderr=str( error ))


# -- Phases ----------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records which unit, which verb, under what options -- nothing more."""

    def __init__( self, engineer: str, request: MechanicRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which service, action and scope this admin run is for" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, service=r.service, action=r.action, scope=r.scope,
                   sudo=r.sudo, dry_run=r.dry_run )


class ValidatePhase( LifecycleLogged, IPhase ):
    """The gate: refuses anything off the allowed-verb list or an unparsable
    unit name before a single subprocess is spawned."""

    def __init__( self, request: MechanicRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._request = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="validate", kind="code", owner="system_mechanic",
                            description="Confirm the action is on the allowed, non-destructive list and "
                                        "the service name is well-formed, before anything runs" )

    def act( self, handle: IPhaseHandle ) -> tuple[ bool, str ]:
        reasons = []
        if self._request.action not in ALLOWED_ACTIONS:
            reasons.append( f"action '{ self._request.action }' is not one of { sorted( ALLOWED_ACTIONS )}" )
        if not SERVICE_NAME_RE.match( self._request.service ):
            reasons.append( f"service name '{ self._request.service }' is not a plausible systemd unit name" )
        reason = "; ".join( reasons )
        handle.log( ok=not reasons, reason=reason or "allowed" )
        return not reasons, reason


class StatusPhase( LifecycleLogged, IPhase ):
    """One read of the unit's state -- used both before and after `execute`."""

    def __init__( self, name: str, why: str, reader: IServiceStatusReader, scope: str, service: str,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._name    = name
        self._why     = why
        self._reader  = reader
        self._scope   = scope
        self._service = service

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="systemctl", description=self._why )

    def act( self, handle: IPhaseHandle ) -> ServiceStatus:
        status = self._reader.read( self._scope, self._service )
        handle.log( active=status.active, exit_code=status.exit_code )
        return status


class ExecutePhase( LifecycleLogged, IPhase ):
    """The one phase that changes anything on the box. A dry run of a
    mutating action never reaches the runner's subprocess call; a read-only
    action always runs for real, because reading changes nothing."""

    def __init__( self, runner: IServiceActionRunner, request: MechanicRequest, effective_dry_run: bool,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._runner  = runner
        self._request = request
        self._dry_run = effective_dry_run

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="execute", kind="code", owner="systemctl",
                            description="Run the requested systemctl action -- the one place this ADW "
                                        "changes anything on the box" )

    def act( self, handle: IPhaseHandle ) -> ActionResult:
        r = self._request
        result = self._runner.run( r.scope, r.action, r.service, r.sudo, self._dry_run )
        handle.log( dry_run=self._dry_run, ok=result.ok, exit_code=result.exit_code,
                   command=" ".join( result.command ), stdout=result.stdout[ :500 ], stderr=result.stderr[ :500 ])
        return result


# -- Argument parsing --------------------------------------------------------

class ArgparseSystemMechanicParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> MechanicRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", help="inline text or a path to a prompt file; why this admin action is being run" )
        parser.add_argument( "--service", required=True,
                             help="the systemd unit to act on, e.g. toyota-receptionist or nginx.service" )
        parser.add_argument( "--action", default="restart", choices=sorted( ALLOWED_ACTIONS ),
                             help="the systemctl verb to run; never a file operation" )
        parser.add_argument( "--scope", default="system", choices=[ "system", "user" ],
                             help="systemctl --user session manager, or the system one (default)" )
        parser.add_argument( "--no-sudo", action="store_true",
                             help="skip the 'sudo -n' prefix for a --scope system action" )
        parser.add_argument( "--dry-run", action="store_true",
                             help="for a mutating action, report the command instead of running it" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return MechanicRequest( prompt=utils.resolve_prompt( parsed.prompt ), service=parsed.service,
                                action=parsed.action, scope=parsed.scope, sudo=not parsed.no_sudo,
                                dry_run=parsed.dry_run, message_out=parsed.message_out,
                                config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class SystemMechanicApplication( LifecycleLogged, IApplication ):
    """Validate, read, act, read again. Depends only on interfaces (DIP)."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  status_reader:   IServiceStatusReader,
                  action_runner:   IServiceActionRunner,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._status_reader   = status_reader
        self._action_runner   = action_runner

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        log: ILifecycleLog = ConsoleLifecycleLog( run )
        execute = lambda phase: self._phase_executor.execute( run, phase )

        execute( RequestCapturePhase( run.engineer, request, log ))

        ok, reason = execute( ValidatePhase( request, log ))
        if not ok:
            return self._finish( run, request, False, f"Refused: { reason }" )

        before = execute( StatusPhase( "status_before", "Read the unit's state before touching it",
                                       self._status_reader, request.scope, request.service, log ))

        effective_dry_run = request.dry_run and request.action in MUTATING_ACTIONS
        result = execute( ExecutePhase( self._action_runner, request, effective_dry_run, log ))

        after = execute( StatusPhase( "status_after",
                                      "Read the unit's state again, so the outcome is observed rather than assumed",
                                      self._status_reader, request.scope, request.service, log ))

        if effective_dry_run:
            return self._finish( run, request, True,
                                 f"Dry run: would { request.action } { request.service } ({ request.scope }); "
                                 f"currently { before.active }." )
        if not result.ok:
            return self._finish( run, request, False,
                                 f"{ request.action } { request.service } failed (exit { result.exit_code }). "
                                 f"{ before.active } -> { after.active }. { ( result.stderr or result.stdout )[ :300 ]}" )
        return self._finish( run, request, True,
                             f"{ request.action } { request.service } ({ request.scope }) done: "
                             f"{ before.active } -> { after.active }." )

    @staticmethod
    def _finish( run: Run, request: MechanicRequest, accepted: bool, message: str ) -> int:
        print( message )
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )
        return run.finish( accepted=accepted, reason=message.splitlines()[ 0 ])


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return SystemMechanicApplication(
        argument_parser = ArgparseSystemMechanicParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(             bootstrap_log ),
        session_factory = SessionRunFactory(            bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(    bootstrap_log ),
        status_reader   = SystemctlStatusReader( DEFAULT_TIMEOUT_SECONDS, bootstrap_log ),
        action_runner   = SystemctlActionRunner( DEFAULT_TIMEOUT_SECONDS, bootstrap_log ),
        logger          =                                bootstrap_log, )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

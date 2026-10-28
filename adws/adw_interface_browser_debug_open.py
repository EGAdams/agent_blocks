#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface Browser Debug Open -- open a URL in a Windows Chrome that a
later workflow can attach to, phase by phase, written in the interface style.

The sibling of `adw_interface_browser_open.py`, differing in one way: that one
hands the link to whatever Windows opens links with and can never touch the
window again; this one starts Chrome itself with a DevTools debug port, so
`adw_interface_browser_click.py` can press a button in it afterwards. Use it
for any page where you type something by hand and want a workflow to carry on.

This Chrome has its own profile (`%LOCALAPPDATA%\\adw-chrome-debug`) because
Chrome refuses a debug port on your everyday profile. So it starts signed out
of everything, remembers what you do in it from then on, and none of your
normal Chrome's tabs or sign-ins are exposed to the port. The port listens on
this machine's loopback only, but anything running on this machine can drive
a page in that window while it is open -- close it when you are done banking.

Program to the interface: `DebugOpenApplication` holds nothing but
interfaces -- its phases come from an `IDebugOpenPhaseFactory`, its wording
from an `IDebugOpenReporter` -- and `build_application()` is the one place
that names a concrete class. The Chrome command line is assembled by an
immutable Builder (`browser_session/chrome_command.py`).

Five phases:

* `request`  (engineer) -- record what is being opened.
* `validate` (code) -- the same scheme/host check as the browser-open ADW,
  `map_open.validate()`, reused rather than copied.
* `probe`    (code) -- is the debug Chrome already up? Then this only adds a tab.
* `launch`   (code) -- chrome.exe with the port and the profile, detached.
* `ready`    (code) -- wait until the port answers, so the next workflow can attach.

Usage:
    uv run adws/adw_interface_browser_debug_open.py "<why>" --url "https://secure.53.com/olb/login" \\
        [--port 9222] [--dry-run] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(validate) -> code(probe) -> code(launch) -> code(ready)
"""

from __future__ import annotations

import argparse
import sys
from typing import Protocol

from pydantic import BaseModel

from sssf_roles import ( IApplication, IArgumentParser, IConfigLoader, ILifecycleLog, IPhase,
                         IPhaseExecutor, IPhaseHandle, ISessionFactory, LifecycleLogged,
                         ManualContextPhaseExecutor, PrintLifecycleLog, SessionRunFactory,
                         SssfConfigLoader )

from adw_modules import map_open, utils
from adw_modules.data_types import LinkCheck, MapOpenRequest, PhaseParams
from adw_modules.runner import Run

from adw_plumbing.interfaces import IMessageSinkFactory, ISessionLogFactory
from adw_plumbing.message_sinks import PathMessageSinkFactory
from adw_plumbing.session_logs import ConsoleSessionLogFactory

from browser_session.chrome_command import ChromeCommandBuilder
from browser_session.chrome_launch import WINDOWS_CHROME, DebugChromeLauncher
from browser_session.endpoint_probe import DevToolsEndpointProbe
from browser_session.interfaces import IChromeLauncher, IDebugOpenReporter, IEndpointProbe, IReadinessWaiter
from browser_session.json_fetcher import UrllibJsonFetcher
from browser_session.models import ChromeLaunchResult, DebugEndpoint, EndpointCheck
from browser_session.process_spawner import DetachedProcessSpawner
from browser_session.profile_dir import WindowsLocalAppDataProfile
from browser_session.readiness import PollingReadinessWaiter
from browser_session.reports import PlainDebugOpenReporter
from browser_session.system_clock import SystemClock

REQUIRED_AGENTS: list[ str ] = []   # every phase here is code; no judgement is needed


# -- Value (not behavior) ----------------------------------------------------

class DebugOpenRequest( BaseModel ):
    """Parsed command-line intent."""
    prompt:      str
    url:         str
    port:        int
    dry_run:     bool
    message_out: str
    config:      str
    adw_id:      str | None


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records what is being opened, nothing more."""

    def __init__( self, engineer: str, request: DebugOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which URL is being opened in the debuggable Chrome, and on which port" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, url=r.url or "(none)", port=r.port, dry_run=r.dry_run )


class ValidateUrlPhase( LifecycleLogged, IPhase ):
    """Scheme-checks the link through `map_open.validate()`, unchanged."""

    def __init__( self, run: Run, url: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run = run
        self._url = url

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="validate", kind="code", owner="browser",
                            description="Decide whether the URL is safe to hand a browser, before any process is spawned" )

    def act( self, handle: IPhaseHandle ) -> LinkCheck:
        check = map_open.validate( self._run, MapOpenRequest( url=self._url ))
        handle.log( openable=check.openable, target=check.target or "(none)" )
        return check


class ProbeEndpointPhase( LifecycleLogged, IPhase ):
    """Notes whether the debug Chrome is already running. Informational:
    launching again only adds a tab to it, so nothing branches on this."""

    def __init__( self, probe: IEndpointProbe, endpoint: DebugEndpoint, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._probe    = probe
        self._endpoint = endpoint

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="probe", kind="code", owner="browser",
                            description="Note whether the debuggable Chrome is already running on this port" )

    def act( self, handle: IPhaseHandle ) -> EndpointCheck:
        check = self._probe.check( self._endpoint )
        handle.log( already_running=check.reachable, browser=check.browser or "(none)" )
        return check


class LaunchPhase( LifecycleLogged, IPhase ):
    """Starts Chrome with the debug port, the profile and the URL, detached."""

    def __init__( self, launcher: IChromeLauncher, url: str, endpoint: DebugEndpoint,
                  dry_run: bool, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._launcher = launcher
        self._url      = url
        self._endpoint = endpoint
        self._dry_run  = dry_run

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="launch", kind="code", owner="browser",
                            description="Start Windows Chrome with a DevTools debug port and its own profile at the URL, detached" )

    def act( self, handle: IPhaseHandle ) -> ChromeLaunchResult:
        if self._dry_run:
            result = ChromeLaunchResult( launched=False, reason="dry run: spawned nothing" )
        else:
            result = self._launcher.launch( self._url, self._endpoint )
        handle.log( launched=result.launched, reason=result.reason or "-" )
        return result


class ReadyPhase( LifecycleLogged, IPhase ):
    """Waits for the debug port to answer, so 'opened' means 'attachable'."""

    def __init__( self, waiter: IReadinessWaiter, endpoint: DebugEndpoint, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._waiter   = waiter
        self._endpoint = endpoint

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="ready", kind="code", owner="browser",
                            description="Wait for the new Chrome's debug port to answer, so a later run can attach" )

    def act( self, handle: IPhaseHandle ) -> EndpointCheck:
        check = self._waiter.wait( self._endpoint )
        handle.log( reachable=check.reachable, browser=check.browser or "(none)", reason=check.reason or "-" )
        return check


# -- Phase factory -----------------------------------------------------------

class IDebugOpenPhaseFactory( Protocol ):
    """Abstract Factory for this workflow's phases."""

    def request( self, engineer: str, request: DebugOpenRequest, log: ILifecycleLog ) -> IPhase: ...
    def validate( self, run: Run, url: str, log: ILifecycleLog ) -> IPhase: ...
    def probe( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase: ...
    def launch( self, url: str, endpoint: DebugEndpoint, dry_run: bool, log: ILifecycleLog ) -> IPhase: ...
    def ready( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase: ...


class DefaultDebugOpenPhaseFactory:
    """IDebugOpenPhaseFactory over this file's phase classes."""

    def __init__( self, probe: IEndpointProbe, launcher: IChromeLauncher, waiter: IReadinessWaiter ):
        self._probe    = probe
        self._launcher = launcher
        self._waiter   = waiter

    def request( self, engineer: str, request: DebugOpenRequest, log: ILifecycleLog ) -> IPhase:
        return RequestCapturePhase( engineer, request, log )

    def validate( self, run: Run, url: str, log: ILifecycleLog ) -> IPhase:
        return ValidateUrlPhase( run, url, log )

    def probe( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase:
        return ProbeEndpointPhase( self._probe, endpoint, log )

    def launch( self, url: str, endpoint: DebugEndpoint, dry_run: bool, log: ILifecycleLog ) -> IPhase:
        return LaunchPhase( self._launcher, url, endpoint, dry_run, log )

    def ready( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase:
        return ReadyPhase( self._waiter, endpoint, log )


# -- Argument parsing --------------------------------------------------------

class ArgparseDebugOpenArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> DebugOpenRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", help="inline text or a path to a prompt file; why this is being opened" )
        parser.add_argument( "--url", required=True, help="the URL to open" )
        parser.add_argument( "--port", type=int, default=DebugEndpoint().port, help="the DevTools debug port" )
        parser.add_argument( "--dry-run", action="store_true", help="validate and probe, but spawn nothing" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return DebugOpenRequest( prompt=utils.resolve_prompt( parsed.prompt ), url=parsed.url,
                                 port=parsed.port, dry_run=parsed.dry_run,
                                 message_out=parsed.message_out, config=parsed.config,
                                 adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class DebugOpenApplication( LifecycleLogged, IApplication ):
    """Orchestrates the five-phase run. Holds interfaces and nothing else."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  phases:          IDebugOpenPhaseFactory,
                  reporter:        IDebugOpenReporter,
                  session_logs:    ISessionLogFactory,
                  sinks:           IMessageSinkFactory,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._phases          = phases
        self._reporter        = reporter
        self._session_logs    = session_logs
        self._sinks           = sinks

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        accepted, message = self._open( run, request, self._session_logs.create( run ))
        self._sinks.for_path( request.message_out ).deliver( message )
        return run.finish( accepted=accepted, reason=message )

    def _open( self, run: Run, request: DebugOpenRequest, log: ILifecycleLog ) -> tuple[ bool, str ]:
        execute, phases, report = self._phase_executor.execute, self._phases, self._reporter
        endpoint = DebugEndpoint( port=request.port )

        execute( run, phases.request( run.engineer, request, log ))
        link = execute( run, phases.validate( run, request.url, log ))
        if not link.openable:
            return False, report.refused( link.reason )
        before   = execute( run, phases.probe( endpoint, log ))
        launched = execute( run, phases.launch( link.target, endpoint, request.dry_run, log ))
        if request.dry_run:
            return True, report.dry_run( link.target, endpoint.port )
        if not launched.launched:
            return False, report.not_launched( launched.reason )
        ready = execute( run, phases.ready( endpoint, log ))
        if not ready.reachable:
            return False, report.never_ready( link.target, endpoint.port )
        return True, report.opened( link.target, endpoint.port, before.reachable )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    probe = DevToolsEndpointProbe( UrllibJsonFetcher())
    base  = ( ChromeCommandBuilder()
              .executable( WINDOWS_CHROME )
              .profile( WindowsLocalAppDataProfile().resolve())
              .flag( "--no-first-run" )
              .flag( "--no-default-browser-check" ))
    phases = DefaultDebugOpenPhaseFactory(
        probe    = probe,
        launcher = DebugChromeLauncher( base, DetachedProcessSpawner()),
        waiter   = PollingReadinessWaiter( probe, SystemClock()),
    )
    return DebugOpenApplication(
        argument_parser = ArgparseDebugOpenArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(                bootstrap_log ),
        session_factory = SessionRunFactory(               bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(      bootstrap_log ),
        phases          = phases,
        reporter        = PlainDebugOpenReporter(),
        session_logs    = ConsoleSessionLogFactory(),
        sinks           = PathMessageSinkFactory(),
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Interface Browser Click -- press one named button in a Chrome window
the user already has open, phase by phase, written in the interface style.

The first use: you open Fifth Third's sign-in page with
`adw_interface_browser_debug_open.py`, type your own username and password
into it, and this presses "Log In" for you. It never types, reads or stores a
credential -- the only thing it does to the page is one click.

"An already-open window" is the hard part, and the answer is the window's
DevTools port: the debug-open ADW starts Chrome with `--remote-debugging-port`
and a profile of its own, and this run attaches selenium-headless's
`WebBrowserManager` to that port instead of letting it launch a fresh
browser (`browser_session/session_attacher.py`). A Chrome opened any other way
-- double-clicked, or by `adw_interface_browser_open.py` -- has no such port
and cannot be steered by anything; `probe` says so and names the fix.

Seven phases, each one job, each over an interface from `browser_session.interfaces`:

* `request`        (engineer) -- record which button, and whether to really press it.
* `resolve_target` (code) -- look the button up in `browser_session.site_targets`.
* `probe`          (code) -- is a debuggable Chrome listening? Instant, and
  asked first because chromedriver pointed at a dead port hangs for a minute.
* `attach`         (code) -- WebBrowserManager onto that Chrome.
* `select_tab`     (code) -- the tab on the target's site (Strategy).
* `locate`         (code) -- the button, by each of the target's locators in
  turn (Chain of Responsibility over Strategies).
* `click`          (code) -- press it (Command), or on --dry-run don't (Null Object).

Program to the interface: `BrowserClickApplication` holds nothing but
interfaces. Its phases come from an `IClickPhaseFactory`, the tab selector,
locator chain and clicker from an `IClickPartsFactory` (both Abstract
Factories), the buttons from an `ITargetCatalog`, the wording from an
`IClickReporter`, and `build_application()` is the one place that names a
concrete class.

The browser is always released, never quit: it is the user's window.

Usage:
    uv run adws/adw_interface_browser_click.py "<why>" [--target fifth_third_login] \\
        [--port 9222] [--dry-run] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(resolve_target) -> code(probe) -> code(attach)
        -> code(select_tab) -> code(locate) -> code(click)
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

from adw_modules import utils
from adw_modules.data_types import PhaseParams
from adw_modules.runner import Run

from adw_plumbing.interfaces import IMessageSinkFactory, ISessionLogFactory
from adw_plumbing.message_sinks import PathMessageSinkFactory
from adw_plumbing.session_logs import ConsoleSessionLogFactory

from browser_session.detach_policies import StopDriverServiceDetach
from browser_session.endpoint_probe import DevToolsEndpointProbe
from browser_session.headless_manager_factory import SeleniumHeadlessManagerFactory
from browser_session.interfaces import ( IBrowserAttacher, IBrowserSession, IClickAction, IClickPartsFactory,
                                         IClickReporter, IElementFinder, IEndpointProbe, ILocatorChain,
                                         IPageElement, ITabNavigator, ITabSelector, ITargetCatalog )
from browser_session.json_fetcher import UrllibJsonFetcher
from browser_session.locator_registry import default_registry
from browser_session.models import ClickResult, ClickTarget, DebugEndpoint, EndpointCheck, LocateResult, TabChoice
from browser_session.parts_factory import DefaultClickPartsFactory
from browser_session.reports import PlainClickReporter
from browser_session.selenium_driver_factory import SeleniumAttachedDriverFactory
from browser_session.session_attacher import ManagerSessionAttacher
from browser_session.site_targets import ALL_TARGETS
from browser_session.system_clock import SystemClock
from browser_session.target_catalog import InMemoryTargetCatalog

REQUIRED_AGENTS: list[ str ] = []   # every phase here is code; no judgement is needed


# -- Value (not behavior) ----------------------------------------------------

class BrowserClickRequest( BaseModel ):
    """Parsed command-line intent."""
    prompt:      str
    target:      str
    port:        int
    dry_run:     bool
    message_out: str
    config:      str
    adw_id:      str | None


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records which button is to be pressed and how, nothing more."""

    def __init__( self, engineer: str, request: BrowserClickRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which button is to be pressed, on which debug port, and whether for real" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, target=r.target, port=r.port, dry_run=r.dry_run )


class ResolveTargetPhase( LifecycleLogged, IPhase ):
    """Looks the button up by name in the catalog. Returns None if unknown."""

    def __init__( self, name: str, catalog: ITargetCatalog, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._name    = name
        self._catalog = catalog

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="resolve_target", kind="code", owner="browser",
                            description="Look the named button up in the site-target catalog" )

    def act( self, handle: IPhaseHandle ) -> ClickTarget | None:
        target = self._catalog.get( self._name )
        handle.log( found=target is not None, host=target.host_suffix if target else "(none)",
                    known=", ".join( self._catalog.names()))
        return target


class ProbeEndpointPhase( LifecycleLogged, IPhase ):
    """Asks whether a debuggable Chrome is listening, before Selenium starts."""

    def __init__( self, probe: IEndpointProbe, endpoint: DebugEndpoint, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._probe    = probe
        self._endpoint = endpoint

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="probe", kind="code", owner="browser",
                            description="Check a Chrome with a DevTools debug port is listening, before starting chromedriver" )

    def act( self, handle: IPhaseHandle ) -> EndpointCheck:
        check = self._probe.check( self._endpoint )
        handle.log( reachable=check.reachable, browser=check.browser or "(none)", reason=check.reason or "-" )
        return check


class AttachPhase( LifecycleLogged, IPhase ):
    """Attaches to the running Chrome. Returns (session, error)."""

    def __init__( self, attacher: IBrowserAttacher, endpoint: DebugEndpoint, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._attacher = attacher
        self._endpoint = endpoint

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="attach", kind="code", owner="browser",
                            description="Attach WebBrowserManager to the already-running Chrome through its debug port" )

    def act( self, handle: IPhaseHandle ) -> tuple[ IBrowserSession | None, str ]:
        try:
            session = self._attacher.attach( self._endpoint )
        except Exception as e:
            first = str( e ).splitlines()[ 0 ] if str( e ) else ""
            error = f"{ type( e ).__name__ }: { first }"
            handle.log( attached=False, error=error )
            return None, error
        handle.log( attached=True, address=self._endpoint.address )
        return session, ""


class SelectTabPhase( LifecycleLogged, IPhase ):
    """Chooses the tab on the target's site and focuses it."""

    def __init__( self, navigator: ITabNavigator, selector: ITabSelector, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._navigator = navigator
        self._selector  = selector

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="select_tab", kind="code", owner="browser",
                            description="Find the open https tab on the target's site and switch to it" )

    def act( self, handle: IPhaseHandle ) -> TabChoice:
        choice = self._selector.choose( self._navigator.tabs())
        if choice.found:
            self._navigator.focus( choice.handle )
        handle.log( found=choice.found, url=choice.url or "(none)", tabs=len( choice.seen ))
        return choice


class LocatePhase( LifecycleLogged, IPhase ):
    """Finds the button through the target's locator chain."""

    def __init__( self, finder: IElementFinder, chain: ILocatorChain, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._finder = finder
        self._chain  = chain

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="locate", kind="code", owner="browser",
                            description="Find a visible, enabled button by each of the target's locators in turn" )

    def act( self, handle: IPhaseHandle ) -> tuple[ IPageElement | None, LocateResult ]:
        element = self._chain.locate( self._finder )
        result  = LocateResult( found=element is not None, strategy=self._chain.winner, tried=self._chain.tried )
        handle.log( found=result.found, strategy=result.strategy or "(none)", tried=" | ".join( result.tried ))
        return element, result


class ClickPhase( LifecycleLogged, IPhase ):
    """Presses the button -- or, holding the Null Object, doesn't."""

    def __init__( self, element: IPageElement, action: IClickAction, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._element = element
        self._action  = action

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="click", kind="code", owner="browser",
                            description="Click the located button (retrying a blocked click; nothing on --dry-run)" )

    def act( self, handle: IPhaseHandle ) -> ClickResult:
        result = self._action.perform( self._element )
        handle.log( clicked=result.clicked, dry_run=result.dry_run, reason=result.reason or "-" )
        return result


# -- Phase factory -----------------------------------------------------------

class IClickPhaseFactory( Protocol ):
    """Abstract Factory for this workflow's phases, so the orchestrator
    builds a run out of interfaces and never names a phase class."""

    def request( self, engineer: str, request: BrowserClickRequest, log: ILifecycleLog ) -> IPhase: ...
    def resolve_target( self, name: str, log: ILifecycleLog ) -> IPhase: ...
    def probe( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase: ...
    def attach( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase: ...
    def select_tab( self, session: IBrowserSession, target: ClickTarget, log: ILifecycleLog ) -> IPhase: ...
    def locate( self, session: IBrowserSession, target: ClickTarget, log: ILifecycleLog ) -> IPhase: ...
    def click( self, element: IPageElement, dry_run: bool, log: ILifecycleLog ) -> IPhase: ...


class DefaultClickPhaseFactory:
    """IClickPhaseFactory over this file's phase classes and the parts they need."""

    def __init__( self, catalog: ITargetCatalog, probe: IEndpointProbe,
                  attacher: IBrowserAttacher, parts: IClickPartsFactory ):
        self._catalog  = catalog
        self._probe    = probe
        self._attacher = attacher
        self._parts    = parts

    def request( self, engineer: str, request: BrowserClickRequest, log: ILifecycleLog ) -> IPhase:
        return RequestCapturePhase( engineer, request, log )

    def resolve_target( self, name: str, log: ILifecycleLog ) -> IPhase:
        return ResolveTargetPhase( name, self._catalog, log )

    def probe( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase:
        return ProbeEndpointPhase( self._probe, endpoint, log )

    def attach( self, endpoint: DebugEndpoint, log: ILifecycleLog ) -> IPhase:
        return AttachPhase( self._attacher, endpoint, log )

    def select_tab( self, session: IBrowserSession, target: ClickTarget, log: ILifecycleLog ) -> IPhase:
        return SelectTabPhase( session, self._parts.tab_selector( target ), log )

    def locate( self, session: IBrowserSession, target: ClickTarget, log: ILifecycleLog ) -> IPhase:
        return LocatePhase( session, self._parts.locator( target ), log )

    def click( self, element: IPageElement, dry_run: bool, log: ILifecycleLog ) -> IPhase:
        return ClickPhase( element, self._parts.click_action( dry_run ), log )


# -- Argument parsing --------------------------------------------------------

class ArgparseBrowserClickArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, catalog: ITargetCatalog, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description
        self._catalog     = catalog

    def parse( self, argv: list[ str ]) -> BrowserClickRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", help="inline text or a path to a prompt file; why this is being clicked" )
        parser.add_argument( "--target", default="fifth_third_login",
                             help=f"which button, from: { ', '.join( self._catalog.names())}" )
        parser.add_argument( "--port", type=int, default=DebugEndpoint().port,
                             help="the debug Chrome's DevTools port" )
        parser.add_argument( "--dry-run", action="store_true", help="find the button but do not click it" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return BrowserClickRequest( prompt=utils.resolve_prompt( parsed.prompt ), target=parsed.target,
                                    port=parsed.port, dry_run=parsed.dry_run,
                                    message_out=parsed.message_out, config=parsed.config,
                                    adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class BrowserClickApplication( LifecycleLogged, IApplication ):
    """Orchestrates the seven-phase run. Holds interfaces and nothing else."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  phases:          IClickPhaseFactory,
                  catalog:         ITargetCatalog,
                  reporter:        IClickReporter,
                  session_logs:    ISessionLogFactory,
                  sinks:           IMessageSinkFactory,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._phases          = phases
        self._catalog         = catalog
        self._reporter        = reporter
        self._session_logs    = session_logs
        self._sinks           = sinks

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        accepted, message = self._click( run, request, self._session_logs.create( run ))
        self._sinks.for_path( request.message_out ).deliver( message )
        return run.finish( accepted=accepted, reason=message )

    def _click( self, run: Run, request: BrowserClickRequest, log: ILifecycleLog ) -> tuple[ bool, str ]:
        execute, phases, report = self._phase_executor.execute, self._phases, self._reporter
        endpoint = DebugEndpoint( port=request.port )

        execute( run, phases.request( run.engineer, request, log ))
        target = execute( run, phases.resolve_target( request.target, log ))
        if target is None:
            return False, report.unknown_target( request.target, self._catalog.names())
        check = execute( run, phases.probe( endpoint, log ))
        if not check.reachable:
            return False, report.no_browser( endpoint.port, check )
        session, error = execute( run, phases.attach( endpoint, log ))
        if session is None:
            return False, report.attach_failed( endpoint.port, error )
        try:
            tab = execute( run, phases.select_tab( session, target, log ))
            if not tab.found:
                return False, report.no_tab( target, tab )
            element, located = execute( run, phases.locate( session, target, log ))
            if element is None:
                return False, report.not_found( target, located )
            clicked = execute( run, phases.click( element, request.dry_run, log ))
            return ( clicked.clicked or clicked.dry_run ), report.finished( target, tab, located, clicked )
        finally:
            session.release()


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    clock   = SystemClock()
    catalog = InMemoryTargetCatalog( ALL_TARGETS )
    phases  = DefaultClickPhaseFactory(
        catalog  = catalog,
        probe    = DevToolsEndpointProbe( UrllibJsonFetcher()),
        attacher = ManagerSessionAttacher( SeleniumAttachedDriverFactory(),
                                           SeleniumHeadlessManagerFactory(),
                                           StopDriverServiceDetach()),
        parts    = DefaultClickPartsFactory( default_registry(), clock ),
    )
    return BrowserClickApplication(
        argument_parser = ArgparseBrowserClickArgumentParser( __doc__, catalog, bootstrap_log ),
        config_loader   = SssfConfigLoader(                   bootstrap_log ),
        session_factory = SessionRunFactory(                  bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(         bootstrap_log ),
        phases          = phases,
        catalog         = catalog,
        reporter        = PlainClickReporter(),
        session_logs    = ConsoleSessionLogFactory(),
        sinks           = PathMessageSinkFactory(),
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

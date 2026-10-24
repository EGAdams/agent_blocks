#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface Browser Open -- open a URL, or a search phrase, in the
Windows browser from WSL, phase by phase, written in the interface style.

Named after `adw_interface_prompt.py`, the template whose roles it programs
against: every collaborator here is an interface from `sssf_roles`, the
phases are objects rather than `with` blocks, and `build_application()` is
the only function allowed to name a concrete class. There is no `with` and
no `yield` in this file -- `ManualContextPhaseExecutor` drives
`run.phase()`'s protocol by hand on every phase's behalf.

Its whole job is to open a browser. This is deliberately NOT a
browser-automation tool: nothing here drives a page once it is open, there
is no Playwright or Selenium dependency, and there is no new launcher. The
actual work -- deciding whether a link is safe to hand a browser, finding
which launcher exists on this box, and spawning it detached -- is
`adw_modules/map_open.py`'s `validate()`, `select_launcher()` and `launch()`,
the same three functions `adw_map_open.py` already runs. This ADW only
supplies the generic front end: a bare URL or a search phrase instead of an
expense's map_link, and operator wording that talks about "a browser" rather
than "this expense's map".

Two invariants carry over unchanged, because they live in the reused module:
never block (the launcher is spawned detached, its exit status unread) and
never raise (a bad URL or a missing launcher is a message, not a crash --
only the RUN's acceptance records whether it actually opened).

Four phases:

* `validate` (code) -- scheme-checks `--url` exactly as given, through
  `map_open.validate()`. A relative path, a `javascript:` string, anything
  without an http(s) host is refused before a process is ever spawned.
* `derive_search` (code) -- only when `--url` was missing or refused and
  `--phrase` was given. Builds a plain web search URL
  (`https://www.google.com/search?q=...`) from the phrase and re-validates
  it the same way, so a search-built link gets no more trust than a typed
  one.
* `select_launcher` (code) -- walks `map_open.LAUNCHERS` and returns the
  first one present on this box. Spawns nothing.
* `launch` (code) -- hands the link to the chosen launcher, detached, via
  `map_open.launch()`, then builds the operator's one line itself rather
  than reusing `map_open`'s wording, which talks about "this expense's map".

No agent phase: opening a link needs no judgement, so `REQUIRED_AGENTS` is
empty and every phase is `kind="code"`.

Usage:
    uv run adws/adw_interface_browser_open.py "<why this is being opened>" \\
        [--url "https://example.com"] [--phrase "wyoming walmart hours"] \\
        [--dry-run] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(validate) [-> code(derive_search)]
        -> code(select_launcher) -> code(launch)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import map_open, utils
from adw_modules.data_types import LaunchResult, LauncherChoice, LinkCheck, MapOpenRequest, PhaseParams
from adw_modules.runner import Run

REQUIRED_AGENTS: list[ str ] = []   # every phase here is code; no judgement is needed


# -- Value (not behavior) ----------------------------------------------------

@dataclass( frozen=True )
class BrowserOpenRequest:
    """Parsed command-line intent. Mirrors sssf_roles.PromptRequest's shape."""
    prompt:      str
    url:         str
    phrase:      str
    dry_run:     bool
    message_out: str
    config:      str
    adw_id:      str | None


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records what is being opened and how, nothing more."""

    def __init__( self, engineer: str, request: BrowserOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which URL or search phrase is being opened and under what options" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, url=r.url or "(none)", phrase=r.phrase or "(none)",
                    dry_run=r.dry_run )


class ValidateUrlPhase( LifecycleLogged, IPhase ):
    """Scheme-checks the candidate link before any process is spawned.

    Reuses `map_open.validate()` as-is: the scheme/host check it performs is
    exactly as valid for a bare URL as for an expense's map_link, and a
    second copy of that check here would be the defect, not the reuse.
    """

    def __init__( self, run: Run, map_request: MapOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="validate", kind="code", owner="browser",
                            description="Decide whether the given URL is safe to hand a browser, before any process is spawned" )

    def act( self, handle: IPhaseHandle ) -> LinkCheck:
        check = map_open.validate( self._run, self._map_request )
        handle.log( openable=check.openable, target=check.target or "(none)" )
        return check


class DeriveSearchPhase( LifecycleLogged, IPhase ):
    """Builds a plain web search URL from a phrase, when no URL was usable.

    Deliberately NOT `map_open.derive_from_place()` -- that builds a Google
    Maps search tuned for an expense's vendor line, and belongs to the
    locked `adw_modules/map_open.py`. This phase's job is a generic web
    search, so it lives here instead of as a second maps-flavoured copy.
    """

    def __init__( self, run: Run, map_request: MapOpenRequest, phrase: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request
        self._phrase      = phrase

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="derive_search", kind="code", owner="browser",
                            description="Build a web search URL from the phrase, since no usable URL was given or stored" )

    def act( self, handle: IPhaseHandle ) -> tuple[ MapOpenRequest, LinkCheck ]:
        query       = " ".join( self._phrase.split())
        url         = f"https://www.google.com/search?q={ quote_plus( query )}"
        new_request = self._map_request.model_copy( update={ "url": url })
        # Same check a typed URL gets -- a search-built link earns no more
        # trust than the stored or typed one did. "place-search" is the
        # closest of map_open's three source literals to what happened here:
        # the link came from a search, not a literal.
        check = map_open.validate( self._run, new_request, source="place-search" )
        handle.log( query=query, url=url, openable=check.openable )
        return new_request, check


class SelectLauncherPhase( LifecycleLogged, IPhase ):
    """Walks `map_open.LAUNCHERS` for the first one present on this box."""

    def __init__( self, run: Run, map_request: MapOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="select_launcher", kind="code", owner="browser",
                            description="Find the first launcher on the ladder that exists on this box, spawning nothing" )

    def act( self, handle: IPhaseHandle ) -> LauncherChoice:
        choice = map_open.select_launcher( self._run, self._map_request )
        handle.log( found=choice.found, launcher=choice.name or "(none)",
                    probed=", ".join( choice.probed ))
        return choice


class LaunchPhase( LifecycleLogged, IPhase ):
    """Hands the link to the chosen launcher, detached, and builds the
    operator's one line itself.

    `map_open.launch()` is reused for the actual spawn -- the never-block,
    never-raise behaviour lives there and nowhere else -- but its own
    `message` talks about "this expense's map", which is wrong in a generic
    browser-open context. The wording here replaces it; the facts
    (`launched`, `launcher`) are untouched.
    """

    def __init__( self, run: Run, map_request: MapOpenRequest, check: LinkCheck,
                  choice: LauncherChoice, source: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request
        self._check       = check
        self._choice      = choice
        self._source      = source

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="launch", kind="code", owner="browser",
                            description="Hand the link to the chosen launcher detached, and produce the operator's one line" )

    def act( self, handle: IPhaseHandle ) -> tuple[ LaunchResult, str ]:
        result  = map_open.launch( self._run, self._map_request, self._check, self._choice )
        message = self._operator_message( result )
        handle.log( launched=result.launched, launcher=result.launcher or "(none)", message=message )
        return result, message

    def _operator_message( self, result: LaunchResult ) -> str:
        check, choice = self._check, self._choice
        if not check.openable:
            return "Could not open a browser: no usable URL or search phrase was given."
        if not choice.found:
            return ( f"No Windows browser launcher found (tried { ', '.join( choice.probed )}). "
                     f"Open manually: { check.target }" )
        if not result.launched:
            return result.message   # "Could not start X: <error>" -- already generic
        via = f"via { choice.name }"
        if self._source == "search":
            return f"No URL given -- searched the web instead. Opened { check.target } { via }."
        return f"Opened { check.target } in the Windows browser { via }."


# -- Argument parsing --------------------------------------------------------

class ArgparseBrowserArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> BrowserOpenRequest:
        parser = argparse.ArgumentParser( description=self._description )
        parser.add_argument( "prompt", help="inline text or a path to a prompt file; why this is being opened" )
        parser.add_argument( "--url", default="", help="the URL to open directly" )
        parser.add_argument( "--phrase", default="",
                             help="a search phrase to look up when there is no usable URL" )
        parser.add_argument( "--dry-run", action="store_true", help="resolve a launcher but spawn nothing" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return BrowserOpenRequest( prompt=utils.resolve_prompt( parsed.prompt ), url=parsed.url,
                                   phrase=parsed.phrase, dry_run=parsed.dry_run,
                                   message_out=parsed.message_out, config=parsed.config,
                                   adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class BrowserOpenApplication( LifecycleLogged, IApplication ):
    """Orchestrates the four-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares -- the same exception the
    shipped template itself makes (`IPhaseExecutor.execute(run: Run, ...)`).
    """

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        session_log: ILifecycleLog = ConsoleLifecycleLog( run )
        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log )) # get input
        base  = MapOpenRequest( url=request.url, dry_run=request.dry_run )
        check = self._phase_executor.execute( run, ValidateUrlPhase( run, base, session_log ))
        map_request, source = base, "stored"
        if not check.openable and request.phrase.strip():
            map_request, check = self._phase_executor.execute( run, DeriveSearchPhase( run, base, request.phrase, session_log ))
            source = "search"

        choice = self._phase_executor.execute(          run, SelectLauncherPhase( run, map_request, session_log ))
        result, message = self._phase_executor.execute( run, LaunchPhase(         run, map_request, check, choice, source, session_log ))

        # The caller may be a menu rather than a terminal. It reads this
        # file, so the operator's line is a contract -- same rule as
        # adw_map_open.py and adw_calculate_distance.py.
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )

        # A dry run resolved everything a real one would and deliberately
        # spawned nothing, so it is accepted on that basis -- not on
        # `launched`, which it honestly reports as false.
        accepted = result.launched or ( request.dry_run and check.openable and choice.found )
        return run.finish( accepted=accepted, reason=message )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return BrowserOpenApplication(
        argument_parser = ArgparseBrowserArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(              bootstrap_log          ),
        session_factory = SessionRunFactory(             bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(    bootstrap_log          ),
        logger          =                                bootstrap_log,         )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

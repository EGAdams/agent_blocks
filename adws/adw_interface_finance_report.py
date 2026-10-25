#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql", "openpyxl"]
# ///
"""ADW Interface Finance Report -- rebuild the whole ROL 2025 Finance Report
from the live books and put it on top of the ADW Generated list.

Written in the interface style, like the category page and duplicate check it
sits beside: every collaborator is a role from `sssf_roles`, the phases are
objects rather than `with` blocks, and `build_application()` is the only
function allowed to name a concrete class.

THE BUILD IS ROL_FINANCES' OWN, NOT A COPY OF IT

The report has always been made in three hand-run steps in the `rol_finances`
repository, and this workflow runs exactly those, in order, rather than
restating any of their arithmetic:

1. `receipt_parsing_tools/build_finance_report.py` -- refreshes the workbook's
   database-driven cells (read-only against MySQL; it refuses to run unless
   the connected database is the master on DESKTOP-2OBSQMC).
2. A headless LibreOffice recalculation of that workbook into
   `rol_2025_finance_report_VIEW.xlsx`, so its formulas hold numbers.
3. `receipt_parsing_tools/build_finance_report_html.py` -- writes
   `readable_documents/rol_2025_finance_report.html` from the recalculated copy.

Any step that fails stops the run and nothing is published, so the list never
shows a report built from half-refreshed numbers. LibreOffice runs with a
throwaway profile, because a desktop LibreOffice already open on the same
profile makes `--convert-to` exit silently without converting.

PUBLISHING

The finished page is copied into `adw_data/web_pages/` as
`ROL_2025_Finance_Report_<Month>_<day>_<HH_MM_SS>.html` -- stamped to the
second so an older link never changes under somebody -- and listed with its
received / spent / left over figures, through the category page ADW's own
`HtmlLinkIndex` and `DashboardHostLinkIndex`, imported by file location.

Note that steps 1 and 3 rewrite tracked files in `rol_finances`
(`readable_documents/rol_2025_finance_report_workbook.xlsx`, `..._VIEW.xlsx`
and `.html`), exactly as the hand-run steps always did; committing them is
left to whoever runs this.

Usage:
    uv run adws/adw_interface_finance_report.py "<why this is being run>" \\
        [--books-repo ~/rol_finances] [--soffice soffice] \\
        [--message-out /tmp/line.txt] [--dashboard-host user@host | none] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(refresh) -> code(recalculate) -> code(render)
        -> code(stamp) -> code(publish) -> code(report)
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sssf_roles import (
    ConsoleLifecycleLog,
    IApplication,
    IArgumentParser,
    IConfigLoader,
    ILifecycleLog,
    IPhase,
    IPhaseExecutor,
    IPhaseHandle,
    ISessionFactory,
    LifecycleLogged,
    ManualContextPhaseExecutor,
    PrintLifecycleLog,
    SessionRunFactory,
    SssfConfigLoader,
)

from adw_modules import utils
from adw_modules.data_types import PhaseParams

REQUIRED_AGENTS: list[ str ] = []   # every phase here is code; no judgement is needed

_HERE = Path( __file__ ).resolve().parent


def _category_web_page():
    """Load `adw_interface_category_web_page.py` by location, once, and cache it.

    Same mechanism as `adw_interface_duplicate_check.py` uses, so either may
    load it first and the other reuses the cached module.
    """
    cached = sys.modules.get( "adw_interface_category_web_page" )
    if cached is not None:
        return cached

    path = _HERE / "adw_interface_category_web_page.py"
    if not path.is_file():
        raise ImportError(
            f"Could not find { path }. This workflow publishes with its link "
            "index rather than keeping a second copy of it."
        )
    if str( _HERE ) not in sys.path:
        sys.path.insert( 0, str( _HERE ))
    spec = importlib.util.spec_from_file_location( "adw_interface_category_web_page", path )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not build an import spec for { path }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ "adw_interface_category_web_page" ] = module
    try:
        spec.loader.exec_module( module )
    except BaseException:
        del sys.modules[ "adw_interface_category_web_page" ]
        raise
    return module


_page_module = _category_web_page()

# Borrowed whole. Nothing below re-implements any of it.
HtmlLinkIndex          = _page_module.HtmlLinkIndex
DashboardHostLinkIndex = _page_module.DashboardHostLinkIndex
IPageIndex             = _page_module.IPageIndex
PublishedLink          = _page_module.PublishedLink
DEFAULT_OUT_DIR        = _page_module.DEFAULT_OUT_DIR
DEFAULT_DASHBOARD_HOST = _page_module.DEFAULT_DASHBOARD_HOST
DASHBOARD_DIR          = _page_module.DASHBOARD_DIR

DEFAULT_BOOKS_REPO = Path.home() / "rol_finances"
PAGE_PREFIX        = "ROL_2025_Finance_Report_"

# Where rol_finances keeps the report, relative to its root.
WORKBOOK    = Path( "readable_documents" ) / "rol_2025_finance_report_workbook.xlsx"
VIEW        = Path( "readable_documents" ) / "rol_2025_finance_report_VIEW.xlsx"
REPORT_HTML = Path( "readable_documents" ) / "rol_2025_finance_report.html"
REFRESH     = Path( "receipt_parsing_tools" ) / "build_finance_report.py"
RENDER      = Path( "receipt_parsing_tools" ) / "build_finance_report_html.py"


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class ReportRequest:
    """Parsed command-line intent."""
    prompt:      str
    books_repo:  Path
    soffice:     str
    message_out: str
    config:      str
    adw_id:      str | None


@dataclass( frozen=True )
class StepResult:
    """What one build step said it did."""
    output: str


@dataclass( frozen=True )
class ReportFigures:
    """The headline numbers the page's summary strip shows."""
    received: str
    spent:    str
    left:     str
    cash:     str


# -- Roles -------------------------------------------------------------------

class IBuildStep( Protocol ):
    """One step of rol_finances' report build. Raises RuntimeError on failure."""

    def run( self ) -> StepResult: ...


class IFigureReader( Protocol ):
    """The headline figures off a finished report page."""

    def read( self, page: Path ) -> ReportFigures: ...


class IBuildStepFactory( Protocol ):
    """The build's steps, in order, as (phase name, description, step)."""

    def steps( self, request: ReportRequest, logger: ILifecycleLog ) -> list[ tuple[ str, str, IBuildStep ]]: ...


# -- Adapters ----------------------------------------------------------------

class BooksScriptStep( LifecycleLogged ):
    """Runs one of rol_finances' own scripts with its own interpreter.

    The repo's `.venv` is used, not this script's `uv` environment, because
    the scripts import the repo's `tools` package and its database settings.
    """

    def __init__( self, repo: Path, script: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._repo   = repo
        self._script = script

    def run( self ) -> StepResult:
        python = self._repo / ".venv" / "bin" / "python"
        script = self._repo / self._script
        for needed in ( python, script ):
            if not needed.exists():
                raise RuntimeError( f"{ needed } is not there." )
        try:
            done = subprocess.run([ str( python ), str( script )], cwd=self._repo,
                                  capture_output=True, text=True, timeout=600 )
        except ( OSError, subprocess.TimeoutExpired ) as error:
            raise RuntimeError( f"{ self._script.name } could not run ({ error })." )
        said = ( done.stdout.strip() or done.stderr.strip())
        if done.returncode != 0:
            last = said.splitlines()[ -1 ] if said else f"exit status { done.returncode }"
            raise RuntimeError( f"{ self._script.name } failed: { last }" )
        return StepResult( output=said )


class LibreOfficeRecalcStep( LifecycleLogged ):
    """Recalculates the workbook into the VIEW copy with headless LibreOffice.

    Converted into a scratch folder with a scratch profile, then moved over
    the VIEW file only once the converted copy exists.
    """

    def __init__( self, repo: Path, soffice: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._repo    = repo
        self._soffice = soffice

    def run( self ) -> StepResult:
        workbook = self._repo / WORKBOOK
        if not workbook.is_file():
            raise RuntimeError( f"{ workbook } is not there." )
        if shutil.which( self._soffice ) is None:
            raise RuntimeError( f"LibreOffice ({ self._soffice }) is not installed here." )
        with tempfile.TemporaryDirectory( prefix="adw_finance_report_" ) as scratch:
            folder  = Path( scratch )
            profile = ( folder / "profile" ).as_uri()
            try:
                done = subprocess.run(
                    [ self._soffice, f"-env:UserInstallation={ profile }", "--headless",
                      "--calc", "--convert-to", "xlsx", "--outdir", str( folder / "out" ), str( workbook )],
                    capture_output=True, text=True, timeout=300 )
            except ( OSError, subprocess.TimeoutExpired ) as error:
                raise RuntimeError( f"LibreOffice could not run ({ error })." )
            converted = folder / "out" / workbook.name
            if done.returncode != 0 or not converted.is_file():
                said = ( done.stderr.strip() or done.stdout.strip() or f"exit status { done.returncode }" )
                raise RuntimeError( f"LibreOffice did not recalculate the workbook: { said }" )
            shutil.move( str( converted ), str( self._repo / VIEW ))
        return StepResult( output=str( self._repo / VIEW ))


class SummaryStripFigureReader( LifecycleLogged ):
    """Reads the four tiles of the page's summary strip.

    `build_finance_report_html.py` owns that markup; a page without one is
    not refused, its figures just read as "?".
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def read( self, page: Path ) -> ReportFigures:
        text = page.read_text( encoding="utf-8" )

        def tile( label: str ) -> str:
            found = re.search( rf"<small>{ re.escape( label )}</small><b>([^<]*)</b>", text )
            return html.unescape( found.group( 1 )).strip() if found else "?"

        return ReportFigures( received=tile( "Received" ), spent=tile( "Spent" ),
                              left=tile( "Left over" ), cash=tile( "Cash 12/31/25" ))


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records why the report is being rebuilt, nothing more."""

    def __init__( self, engineer: str, request: ReportRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record why the ROL 2025 Finance Report is being rebuilt" )

    def act( self, handle: IPhaseHandle ) -> None:
        handle.log( input=self._request.prompt, books_repo=str( self._request.books_repo ))


class BuildStepPhase( LifecycleLogged, IPhase ):
    """Runs one build step and records what it said."""

    def __init__( self, name: str, description: str, step: IBuildStep, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._name        = name
        self._description = description
        self._step        = step

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="books", description=self._description )

    def act( self, handle: IPhaseHandle ) -> StepResult:
        result = self._step.run()
        handle.log( output=result.output or "(nothing printed)" )
        return result


class StampPagePhase( LifecycleLogged, IPhase ):
    """Copies the finished report into the ADW Generated folder under a
    name stamped to the second, so it never overwrites an older link."""

    def __init__( self, source: Path, folder: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._source = source
        self._folder = folder

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="stamp", kind="code", owner="books",
                            description="Copy the rebuilt report into the ADW Generated folder under a dated name" )

    def act( self, handle: IPhaseHandle ) -> Path:
        if not self._source.is_file():
            raise RuntimeError( f"The rebuilt report ({ self._source }) is not there." )
        self._folder.mkdir( parents=True, exist_ok=True )
        now  = datetime.now()
        stem = f"{ PAGE_PREFIX }{ now:%B}_{ now.day }_{ now:%H_%M_%S}"
        path = self._folder / f"{ stem }.html"
        n = 2
        while path.exists():
            path = self._folder / f"{ stem }_{ n }.html"
            n += 1
        shutil.copyfile( self._source, path )
        handle.log( path=str( path ), bytes=path.stat().st_size )
        return path.resolve()


class PublishReportPhase( LifecycleLogged, IPhase ):
    """Puts the stamped report at the top of the ADW Generated list."""

    def __init__( self, index: IPageIndex, figures: ReportFigures, page: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._index   = index
        self._figures = figures
        self._page    = page

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="publish", kind="code", owner="books",
                            description="Add a link to the rebuilt report at the top of the ADW Generated list, moving older links down" )

    def act( self, handle: IPhaseHandle ) -> PublishedLink:
        f         = self._figures
        summary   = ( f"ROL 2025 Finance Report -- rebuilt from the expenses database -- "
                      f"received { f.received }, spent { f.spent }, left over { f.left }, cash 12/31/25 { f.cash }" )
        published = self._index.publish( self._page, summary )
        handle.log( index=str( published.index ), link=self._page.name,
                    dashboard=published.note or "(local list is the dashboard's)" )
        return published


class FinanceReportPhase( LifecycleLogged, IPhase ):
    """The one line: the headline figures, and where the page is."""

    def __init__( self, figures: ReportFigures, page: Path, published: PublishedLink, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._figures   = figures
        self._page      = page
        self._published = published

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="books",
                            description="State the report's headline figures and where the page is" )

    def act( self, handle: IPhaseHandle ) -> str:
        f       = self._figures
        message = ( f"ROL 2025 Finance Report rebuilt: received { f.received }, spent { f.spent }, "
                    f"left over { f.left }, cash on hand 12/31/25 { f.cash }. Page: { self._page }." )
        if self._published.note:
            message = f"{ message } { self._published.note }"
        handle.log( message=message )
        return message


# -- Argument parsing --------------------------------------------------------

class ArgparseFinanceReportArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> ReportRequest:
        parser = argparse.ArgumentParser(
            description=self._description, formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="rebuild the 2025 finance report",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--books-repo", default=str( DEFAULT_BOOKS_REPO ),
                             help="the rol_finances checkout whose scripts build the report" )
        parser.add_argument( "--soffice", default="soffice", help="the LibreOffice command" )
        parser.add_argument( "--dashboard-host", default=DEFAULT_DASHBOARD_HOST,
                             help="ssh target whose dashboard shows ADW Generated; 'none' keeps the page local" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return ReportRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                              books_repo=Path( parsed.books_repo ).expanduser().resolve(),
                              soffice=parsed.soffice, message_out=parsed.message_out,
                              config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class FinanceReportApplication( LifecycleLogged, IApplication ):
    """Orchestrates the seven-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  steps:           IBuildStepFactory,
                  figure_reader:   IFigureReader,
                  page_index:      IPageIndex,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._steps           = steps
        self._figure_reader   = figure_reader
        self._page_index      = page_index

    def _say( self, request: ReportRequest, message: str ) -> None:
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )

        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))

        # ************************* MAIN *******************************

        # A step that fails stops everything before anything is published:
        # a half-refreshed report on the list would read as the real one.
        try:
            for name, description, step in self._steps.steps( request, session_log ):
                self._phase_executor.execute( run, BuildStepPhase( name, description, step, session_log ))
            page = self._phase_executor.execute(
                run, StampPagePhase( request.books_repo / REPORT_HTML, DEFAULT_OUT_DIR, session_log ))
        except RuntimeError as refusal:
            message = f"No finance report was published. { refusal }"
            self._say( request, message )
            print( message )
            return run.finish( accepted=False, reason=message )

        figures   = self._figure_reader.read( page )
        published = self._phase_executor.execute( run, PublishReportPhase( self._page_index, figures, page, session_log ))
        message   = self._phase_executor.execute( run, FinanceReportPhase( figures, page, published, session_log ))
        self._say( request, message )
        return run.finish( accepted=page.exists(), reason=message )

        # ************************* END MAIN ***************************


class RolFinancesBuildSteps( LifecycleLogged ):
    """The three steps rol_finances has always run by hand, in that order.

    A factory rather than a list because the repo they run in comes from the
    command line, which is parsed after the application is built.
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def steps( self, request: ReportRequest, logger: ILifecycleLog ) -> list[ tuple[ str, str, IBuildStep ]]:
        repo = request.books_repo
        return [
            ( "refresh", "Refresh the workbook's database-driven cells from the live books (read-only)",
              BooksScriptStep( repo, REFRESH, logger )),
            ( "recalculate", "Recalculate the workbook's formulas with headless LibreOffice into the VIEW copy",
              LibreOfficeRecalcStep( repo, request.soffice, logger )),
            ( "render", "Write the report's HTML page from the recalculated workbook",
              BooksScriptStep( repo, RENDER, logger )),
        ]


def _dashboard_host( argv: list[ str ]) -> str:
    """The --dashboard-host the caller named, read before the index is built."""
    peek = argparse.ArgumentParser( add_help=False )
    peek.add_argument( "--dashboard-host", default=DEFAULT_DASHBOARD_HOST )
    known, _ = peek.parse_known_args( argv )
    return known.dashboard_host


def build_application( argv: list[ str ]) -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    page_index: IPageIndex = HtmlLinkIndex( bootstrap_log )
    host = _dashboard_host( argv )
    if host and host.lower() != "none":
        page_index = DashboardHostLinkIndex( page_index, HtmlLinkIndex( bootstrap_log ), host,
                                             DASHBOARD_DIR, DEFAULT_OUT_DIR, bootstrap_log )
    return FinanceReportApplication(
        argument_parser = ArgparseFinanceReportArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(             bootstrap_log ),
        session_factory = SessionRunFactory(            bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(   bootstrap_log ),
        steps           = RolFinancesBuildSteps(        bootstrap_log ),
        figure_reader   = SummaryStripFigureReader(     bootstrap_log ),
        page_index      = page_index,
        logger          =                               bootstrap_log,            )


if __name__ == "__main__":
    sys.exit( build_application( sys.argv[ 1: ]).run( sys.argv[ 1: ]))

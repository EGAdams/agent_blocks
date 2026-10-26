#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql", "openpyxl"]
# ///
"""ADW Interface Duplicate Check -- read the newest report on the ADW Generated
list, find the rows in it worth a second look, and put a page of just those
rows on top of the list.

Written in the interface style, like the two ADWs it is built from: every
collaborator is a role from `sssf_roles`, the phases are objects rather than
`with` blocks, and `build_application()` is the only function allowed to name
a concrete class.

WHAT IT LOOKS FOR

* Possible duplicates -- the same amount near the same date (the spreadsheet
  ADW's `DuplicateRule`, imported, not copied), except two checks with
  different check numbers, which are two checks and never flagged.
* Warnings -- an expense with no category, one missing its amount or
  description, a date that cannot be right, a row whose description names
  one person and whose category another, and a bookkeeping status that is
  still open (needs a vendor key, waiting for its payment). A catch-all
  category such as "Other" is a fine place to file an expense and is not
  a warning.

Every row on the page is yellow, and hovering it pops up why that expense may
be a problem; the pop-up closes when the pointer leaves the row. "May" is the
word on purpose: two $100 checks in one week can be two gifts. The page says
what to check, never that the books are wrong.

THE REPORT IS THE INPUT, NOT THE DATABASE

The rows are read back off the newest page in `adw_data/web_pages/index.json`,
so the check is of exactly what somebody was shown -- no second query that
could have picked up a row entered since. A page this ADW wrote is skipped
when finding "the newest report", so running it twice checks the same report
twice rather than checking its own output. `--source` names a page instead.

THE SAME PHASES AS THE PAGE IT CHECKS

Rendering, publishing to the ADW Generated list (and mirroring it to the
dashboard host), are `RenderPagePhase`, `PublishLinkPhase`,
`HtmlTablePageRenderer`, `HtmlLinkIndex` and `DashboardHostLinkIndex` from
`adw_interface_category_web_page.py`, imported by file location. The only
renderer change is what the pop-up says: the flag's explanation instead of
the row's status.

Usage:
    uv run adws/adw_interface_duplicate_check.py "<why this is being run>" \\
        [--source adws/adw_data/web_pages/<page>.html] \\
        [--message-out /tmp/line.txt] [--dashboard-host user@host | none] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(locate) -> code(read) -> code(check)
        -> code(render) -> code(publish) -> code(report)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, Sequence

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

    Same mechanism as that file's own `_category_spreadsheet()`, which it runs
    in turn, so the spreadsheet's flag rules come along with it.
    """
    cached = sys.modules.get( "adw_interface_category_web_page" )
    if cached is not None:
        return cached

    path = _HERE / "adw_interface_category_web_page.py"
    if not path.is_file():
        raise ImportError(
            f"Could not find { path }. This workflow renders and publishes with "
            "its phases rather than keeping a second copy of them."
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


_page_module  = _category_web_page()
_sheet_module = _page_module._sheet_module

# Borrowed whole. Nothing below re-implements any of it.
HtmlTablePageRenderer  = _page_module.HtmlTablePageRenderer
HtmlLinkIndex          = _page_module.HtmlLinkIndex
DashboardHostLinkIndex = _page_module.DashboardHostLinkIndex
RenderPagePhase        = _page_module.RenderPagePhase
PublishLinkPhase       = _page_module.PublishLinkPhase
IPageIndex             = _page_module.IPageIndex
PagePlan               = _page_module.PagePlan
PublishedLink          = _page_module.PublishedLink
DEFAULT_OUT_DIR        = _page_module.DEFAULT_OUT_DIR
INDEX_MANIFEST         = _page_module.INDEX_MANIFEST
DEFAULT_DASHBOARD_HOST = _page_module.DEFAULT_DASHBOARD_HOST
DASHBOARD_DIR          = _page_module.DASHBOARD_DIR
CategoryScope          = _sheet_module.CategoryScope
Flag                   = _sheet_module.Flag
IFlagRule              = _sheet_module.IFlagRule
IFlagFinder            = _sheet_module.IFlagFinder
CompositeFlagFinder    = _sheet_module.CompositeFlagFinder
DuplicateRule          = _sheet_module.DuplicateRule
OddDateRule            = _sheet_module.OddDateRule
MislabelRule           = _sheet_module.MislabelRule
UnassignedRule         = _sheet_module.UnassignedRule
InitialsPersonReader   = _sheet_module.InitialsPersonReader
_flag                  = _sheet_module._flag
_amount                = _sheet_module._amount
_money                 = _sheet_module._money
_total                 = _sheet_module._total
_safe_name             = _sheet_module._safe_name

# Every page this ADW writes starts with this, which is how the locator
# tells a duplicate report from a report worth checking.
PAGE_PREFIX = "Duplicates_"


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class CheckRequest:
    """Parsed command-line intent."""
    prompt:      str
    source:      str
    message_out: str
    config:      str
    adw_id:      str | None


@dataclass( frozen=True )
class SourceReport:
    """What a report page showed: its rows, and what it said they were."""
    path:     Path
    org:      str
    label:    str
    year:     int | None
    columns:  tuple[ str, ... ]
    rows:     tuple[ dict[ str, Any ], ... ]


# -- Roles -------------------------------------------------------------------

class IReportLocator( Protocol ):
    """Which report to check. Raises RuntimeError when there is none."""

    def locate( self, source: str ) -> Path: ...


class IReportReader( Protocol ):
    """A report page's rows, as the dicts the flag rules read."""

    def read( self, path: Path ) -> SourceReport: ...


class IFlagExplainer( Protocol ):
    """Why the flags on one row may matter, in words for the pop-up."""

    def explain( self, flags: Sequence[ Flag ], rows_by_id: dict[ int, dict[ str, Any ]] ) -> str: ...


# -- Adapters ----------------------------------------------------------------

class ManifestReportLocator( LifecycleLogged ):
    """The newest page on the ADW Generated list that this ADW did not write.

    Read from `index.json`, the list's own record, rather than by file
    modification time -- a page copied or touched later is not newer.
    """

    def __init__( self, folder: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._folder = folder

    def locate( self, source: str ) -> Path:
        if source:
            path = Path( source ).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError( f"There is no report at { path }." )
            return path
        manifest = self._folder / INDEX_MANIFEST
        try:
            entries = json.loads( manifest.read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            raise RuntimeError( f"The ADW Generated list ({ manifest }) could not be read." )
        for entry in entries if isinstance( entries, list ) else []:
            name = entry.get( "file" ) if isinstance( entry, dict ) else None
            if isinstance( name, str ) and not name.startswith( PAGE_PREFIX ):
                path = self._folder / name
                if path.is_file():
                    return path
        raise RuntimeError( "The ADW Generated list has no report to check yet." )


class _TableParser( HTMLParser ):
    """Collects the page's heading, sub-line, header cells and body rows."""

    def __init__( self ):
        super().__init__( convert_charrefs=True )
        self.heading = ""
        self.subs: list[ str ]               = []
        self.header: list[ str ]             = []
        self.rows: list[ tuple[ dict[ str, str ], list[ str ]]] = []
        self._section = ""
        self._text: list[ str ] | None = None
        self._attrs: dict[ str, str ] = {}
        self._cells: list[ str ] = []
        self._capture = ""

    def handle_starttag( self, tag: str, attrs: list[ tuple[ str, str | None ]]) -> None:
        if tag in ( "thead", "tbody", "tfoot" ):
            self._section = tag
        elif tag == "tr" and self._section == "tbody":
            self._attrs = { k: v or "" for k, v in attrs }
            self._cells = []
        elif tag in ( "th", "td", "h1", "p" ):
            self._capture = tag
            self._text    = []

    def handle_endtag( self, tag: str ) -> None:
        if tag in ( "thead", "tbody", "tfoot" ):
            self._section = ""
        elif tag == "tr" and self._section == "tbody":
            self.rows.append(( self._attrs, self._cells ))
        elif tag == self._capture and self._text is not None:
            text = "".join( self._text ).strip()
            if tag == "th" and self._section == "thead":
                self.header.append( text )
            elif tag == "td" and self._section == "tbody":
                self._cells.append( text )
            elif tag == "h1" and not self.heading:
                self.heading = text
            elif tag == "p":
                self.subs.append( text )
            self._capture, self._text = "", None

    def handle_data( self, data: str ) -> None:
        if self._text is not None:
            self._text.append( data )


class HtmlReportReader( LifecycleLogged ):
    """Reads a page `HtmlTablePageRenderer` wrote back into rows.

    The renderer is the format's owner, so its own tables are inverted here
    rather than restated: header labels back to column names, and a status
    sentence back to the status code it was written from.
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._columns = { label: name for name, label in HtmlTablePageRenderer._LABELS.items() }
        self._codes   = { text: code for code, text in HtmlTablePageRenderer._STATUS_TEXT.items() }

    def _money( self, text: str ) -> Decimal | None:
        cleaned = text.replace( "$", "" ).replace( ",", "" ).strip()
        negative = cleaned.startswith( "(" ) and cleaned.endswith( ")" )
        try:
            value = Decimal( cleaned.strip( "()" ))
        except InvalidOperation:
            return None
        return -value if negative else value

    def _status( self, text: str ) -> str:
        if not text:
            return ""
        if text in self._codes:
            return self._codes[ text ]
        return text[ len( "Status: " ): ] if text.startswith( "Status: " ) else text

    def read( self, path: Path ) -> SourceReport:
        parser = _TableParser()
        parser.feed( path.read_text( encoding="utf-8" ))
        columns = tuple( self._columns.get( label, label.lower()) for label in parser.header )
        if "id" not in columns or "amount" not in columns:
            raise RuntimeError( f"{ path.name } is not a category report this check can read." )

        rows: list[ dict[ str, Any ]] = []
        for attrs, cells in parser.rows:
            if len( cells ) != len( columns ):
                continue   # the empty-category note, or something hand-edited
            row: dict[ str, Any ] = dict( zip( columns, cells ))
            row[ "amount" ] = self._money( row[ "amount" ])
            row[ "status" ] = self._status( attrs.get( "data-status", "" ))
            rows.append( row )

        label   = re.sub( r"\s*\(and \d+ sub-categories\)$", "", parser.heading )
        sub     = parser.subs[ 0 ] if parser.subs else ""
        org     = sub.split( "·" )[ 0 ].strip()
        year    = re.search( r"expenses in (\d{4})", sub )
        rolled  = parser.heading[ len( label ): ].strip()
        return SourceReport( path=path, org=org, label=f"{ label } { rolled }".strip(),
                             year=int( year.group( 1 )) if year else None,
                             columns=columns, rows=tuple( rows ))


class UncategorizedRule( LifecycleLogged ):
    """A row with no category at all, or the query's stand-in for none."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def kind( self ) -> str:
        return "Uncategorized"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:
        return [ _flag( row, self.kind, "This expense has no category" )
                 for row in rows
                 if str( row.get( "category" ) or "" ).strip().casefold() in ( "", "(uncategorized)", "uncategorized" )]


class MissingValueRule( LifecycleLogged ):
    """An amount that is missing or zero, or a blank description.

    Merchant and receipt are not checked: in these books most gifts carry
    the recipient as the category and no merchant, and most have no receipt
    on file, so flagging either would turn every row yellow and hide the
    rows that matter.
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def kind( self ) -> str:
        return "Missing value"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:
        found: list[ Flag ] = []
        for row in rows:
            if row.get( "amount" ) is None:
                found.append( _flag( row, self.kind, "This expense has no amount" ))
            elif _amount( row ) == 0:
                found.append( _flag( row, self.kind, "This expense's amount is $0.00" ))
            if not str( row.get( "description" ) or "" ).strip():
                found.append( _flag( row, self.kind, "This expense has no description" ))
        return found


class DistinctCheckNumberDuplicateRule( DuplicateRule ):
    """The spreadsheet ADW's duplicate rule, less pairs with different check numbers.

    Two checks with two numbers are two pieces of paper, so the pair is not
    a possible duplicate at all -- it is dropped, not explained away in the
    pop-up. A pair where either description has no check number stays in.
    """

    def _may_be_one_payment( self, first: dict[ str, Any ], second: dict[ str, Any ] ) -> bool:
        mine   = self._check_number( first.get( "description" ))
        theirs = self._check_number( second.get( "description" ))
        return not ( mine and theirs and mine != theirs )

    def _check_number( self, text: Any ) -> str:
        found = re.search( r"\bcheck\s*#?\s*(\d+)", str( text or "" ), re.IGNORECASE )
        return found.group( 1 ) if found else ""


class OpenStatusRule( LifecycleLogged ):
    """A bookkeeping status that means the row is not finished yet.

    COUNTERPART_DOCUMENT_LINKED is a finished state, so it is not a warning.
    """

    _OPEN = frozenset({ "NEEDS_VENDOR_KEY", "WAITING_FOR_PAYMENT_COUNTERPART" })

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def kind( self ) -> str:
        return "Open status"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:
        return [ _flag( row, self.kind,
                        HtmlTablePageRenderer._STATUS_TEXT.get( row[ "status" ], row[ "status" ]))
                 for row in rows if row.get( "status" ) in self._OPEN ]


class PlainWordsExplainer( LifecycleLogged ):
    """Each flag's detail, then why that kind of flag matters at tax time.

    A duplicate's partner is spelled out -- date, amount and description --
    so the reader can judge the pair from the pop-up without hunting for it.
    """

    _WHY: dict[ str, str ] = {
        "Possible duplicate":
            "If both rows are one payment entered twice, the total (and any deduction built on it) counts it twice.",
        "Odd date":
            "A wrong date can put this expense in the wrong tax year.",
        "Possible mislabel":
            "The gift may be credited to the wrong person's category.",
        "Unassigned":
            "Nobody has been named as owning this expense yet.",
        "Uncategorized":
            "An uncategorized expense is left out of every category total, so the tax forms will not account for it.",
        "Missing value":
            "A row missing its amount or description cannot be matched to a bank statement or receipt.",
        "Open status":
            "The bookkeeping for this row is not finished.",
    }

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def _partner( self, detail: str,
                  rows_by_id: dict[ int, dict[ str, Any ]] ) -> str:
        found = re.search( r"#(\d+)", detail )
        other = rows_by_id.get( int( found.group( 1 ))) if found else None
        if other is None:
            return detail
        return ( f"{ detail }: { other.get( 'date' )}, { _money( other.get( 'amount' ))}, "
                 f"\"{ other.get( 'description' ) or '(no description)' }\"" )

    def explain( self, flags: Sequence[ Flag ], rows_by_id: dict[ int, dict[ str, Any ]] ) -> str:
        lines: list[ str ] = []
        for flag in flags:
            detail = ( self._partner( flag.detail, rows_by_id )
                       if flag.kind == "Possible duplicate" else flag.detail )
            lines.append( f"{ flag.kind }: { detail }. { self._WHY.get( flag.kind, '' )}".strip())
        return "\n".join( lines )


class FlaggedRowsPageRenderer( HtmlTablePageRenderer ):
    """The category page's renderer, with the pop-up saying why, not status.

    Every row this page gets is flagged, so every row is yellow; the rows'
    `why` is the pop-up text. Lines in it stay on their own lines.
    """

    _LABELS     = { **HtmlTablePageRenderer._LABELS, "issue": "Issue" }
    _STYLE      = HtmlTablePageRenderer._STYLE + ".tip { white-space: pre-line; }\n"
    _KEY_WORDS  = "may be a problem -- hover a yellow row to see why."
    _EMPTY_TEXT = "No possible duplicates or warnings were found in this report ({when})."
    _MADE_BY    = ( "Rows read from the ADW Generated report named above. Generated {generated} by\n"
                    "adw_interface_duplicate_check.py, run {adw_id}." )

    def _status( self, row: dict[ str, Any ] ) -> str:
        return str( row.get( "why" ) or "" )


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records which report is to be checked, nothing more."""

    def __init__( self, engineer: str, request: CheckRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which ADW Generated report is to be checked for duplicates and warnings" )

    def act( self, handle: IPhaseHandle ) -> None:
        handle.log( input=self._request.prompt, source=self._request.source or "(newest on the list)" )


class LocateReportPhase( LifecycleLogged, IPhase ):
    """Finds the report to check."""

    def __init__( self, locator: IReportLocator, source: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._locator = locator
        self._source  = source

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="locate", kind="code", owner="books",
                            description="Find the newest report on the ADW Generated list that is not itself a duplicate check" )

    def act( self, handle: IPhaseHandle ) -> Path:
        path = self._locator.locate( self._source )
        handle.log( report=str( path ))
        return path


class ReadReportPhase( LifecycleLogged, IPhase ):
    """Reads the report's rows back off the page."""

    def __init__( self, reader: IReportReader, path: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._reader = reader
        self._path   = path

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="read", kind="code", owner="books",
                            description="Read every expense row the report showed, with its amount and status" )

    def act( self, handle: IPhaseHandle ) -> SourceReport:
        report = self._reader.read( self._path )
        handle.log( label=report.label, year=report.year if report.year is not None else "(all)",
                    rows=len( report.rows ), total=_money( _total( list( report.rows ))))
        return report


class CheckRowsPhase( LifecycleLogged, IPhase ):
    """Runs every rule and keeps only the flagged rows, each with its why.

    Possible duplicates come first, ordered by amount then date so each pair
    sits together; every other warning follows in date order.
    """

    def __init__( self, finder: IFlagFinder, explainer: IFlagExplainer,
                  report: SourceReport, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._finder    = finder
        self._explainer = explainer
        self._report    = report

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="check", kind="code", owner="books",
                            description="Flag possible duplicates, missing values, missing or vague categories, odd dates and open statuses" )

    def act( self, handle: IPhaseHandle ) -> list[ dict[ str, Any ]]:
        rows       = list( self._report.rows )
        rows_by_id = { int( r[ "id" ]): r for r in rows if str( r.get( "id" ) or "" ).isdigit() }
        by_row: dict[ int, list[ Flag ]] = {}
        for flag in self._finder.find( rows ):
            by_row.setdefault( flag.expense_id, [] ).append( flag )

        flagged: list[ dict[ str, Any ]] = []
        for expense_id, flags in by_row.items():
            row = rows_by_id.get( expense_id )
            if row is None:
                continue
            kinds = list( dict.fromkeys( f.kind for f in flags ))
            flagged.append({ **row, "issue": ", ".join( kinds ),
                             "why": self._explainer.explain( flags, rows_by_id )})

        def order( row: dict[ str, Any ]) -> tuple:
            duplicate = "Possible duplicate" in row[ "issue" ]
            return ( not duplicate, _amount( row ) if duplicate else 0, str( row.get( "date" )), int( row[ "id" ]))
        flagged.sort( key=order )

        counts: dict[ str, int ] = {}
        for flags in by_row.values():
            for kind in dict.fromkeys( f.kind for f in flags ):
                counts[ kind ] = counts.get( kind, 0 ) + 1
        handle.log( checked=len( rows ), flagged=len( flagged ), **{ k.replace( " ", "_" ).lower(): v for k, v in counts.items() })
        return flagged


class DuplicateReportPhase( LifecycleLogged, IPhase ):
    """The one line: how many rows of which report, and where the page is."""

    def __init__( self, report: SourceReport, flagged: list[ dict[ str, Any ]],
                  written: Path, published: PublishedLink, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._report    = report
        self._flagged   = flagged
        self._written   = written
        self._published = published

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="books",
                            description="State how many rows of which report were flagged, and where the page is" )

    def act( self, handle: IPhaseHandle ) -> str:
        r          = self._report
        duplicates = sum( 1 for row in self._flagged if "Possible duplicate" in row[ "issue" ])
        message    = ( f"{ r.org }: checked { len( r.rows )} rows of { r.path.name } -- "
                       f"{ len( self._flagged )} may be a problem ({ duplicates } possible duplicates, "
                       f"{ len( self._flagged ) - duplicates } other warnings). Page: { self._written }." )
        if self._published.note:
            message = f"{ message } { self._published.note }"
        handle.log( message=message )
        return message


# -- Argument parsing --------------------------------------------------------

class ArgparseDuplicateCheckArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> CheckRequest:
        parser = argparse.ArgumentParser(
            description=self._description, formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="check the latest report for duplicates",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--source", default="",
                             help="the report page to check; default the newest on the ADW Generated list" )
        parser.add_argument( "--dashboard-host", default=DEFAULT_DASHBOARD_HOST,
                             help="ssh target whose dashboard shows ADW Generated; 'none' keeps the page local" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return CheckRequest( prompt=utils.resolve_prompt( parsed.prompt ), source=parsed.source,
                             message_out=parsed.message_out, config=parsed.config,
                             adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class DuplicateCheckApplication( LifecycleLogged, IApplication ):
    """Orchestrates the seven-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  locator:         IReportLocator,
                  reader:          IReportReader,
                  finder:          IFlagFinder,
                  explainer:       IFlagExplainer,
                  renderer:        Any,
                  page_index:      IPageIndex,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._locator         = locator
        self._reader          = reader
        self._finder          = finder
        self._explainer       = explainer
        self._renderer        = renderer
        self._page_index      = page_index

    def _say( self, request: CheckRequest, message: str ) -> None:
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )

        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))

        # No report, or a page that is not one, is a real answer -- and nothing
        # is written, because an empty check of nothing would read as "clean".
        try:
            source = self._phase_executor.execute( run, LocateReportPhase( self._locator, request.source, session_log ))
            report = self._phase_executor.execute( run, ReadReportPhase( self._reader, source, session_log ))
        except RuntimeError as refusal:
            message = f"No duplicate check was written. { refusal }"
            self._say( request, message )
            print( message )
            return run.finish( accepted=False, reason=message )

        # ************************* MAIN *******************************

        flagged   = self._phase_executor.execute( run, CheckRowsPhase( self._finder, self._explainer, report, session_log ))
        plan      = self._plan( report, flagged, run.adw_id )
        written   = self._phase_executor.execute( run, RenderPagePhase( self._renderer, plan, flagged, session_log ))
        published = self._phase_executor.execute( run, PublishLinkPhase( self._page_index, plan, flagged, written, session_log ))
        message   = self._phase_executor.execute( run, DuplicateReportPhase( report, flagged, written, published, session_log ))
        self._say( request, message )
        return run.finish( accepted=written.exists(), reason=message )

        # ************************* END MAIN ***************************

    def _plan( self, report: SourceReport, flagged: list[ dict[ str, Any ]], adw_id: str ) -> PagePlan:
        """The page goes beside the report it checks, named for it and stamped
        to the second, so it never overwrites a page an older link points at."""
        columns = list( report.columns )
        columns.insert( 1, "issue" )
        now  = datetime.now()
        stem = f"{ PAGE_PREFIX }{ _safe_name( report.label )}_{ now:%B}_{ now.day }_{ now:%H_%M_%S}"
        path = DEFAULT_OUT_DIR / f"{ stem }.html"
        n = 2
        while path.exists():
            path = DEFAULT_OUT_DIR / f"{ stem }_{ n }.html"
            n += 1
        label = f"Possible duplicates & warnings -- { report.label } (from { report.path.name })"
        return PagePlan( org=report.org, category=CategoryScope( ids=(), label=label, children=0 ),
                         year=report.year, columns=tuple( columns ), adw_id=adw_id,
                         path=path.resolve())


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
    return DuplicateCheckApplication(
        argument_parser = ArgparseDuplicateCheckArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(             bootstrap_log ),
        session_factory = SessionRunFactory(            bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(   bootstrap_log ),
        locator         = ManifestReportLocator(        DEFAULT_OUT_DIR, bootstrap_log ),
        reader          = HtmlReportReader(             bootstrap_log ),
        finder          = CompositeFlagFinder([ DistinctCheckNumberDuplicateRule( bootstrap_log ),
                                                MissingValueRule(     bootstrap_log ),
                                                UncategorizedRule(    bootstrap_log ),
                                                OddDateRule(          bootstrap_log, date.today()),
                                                MislabelRule(         bootstrap_log, InitialsPersonReader( bootstrap_log )),
                                                UnassignedRule(       bootstrap_log ),
                                                OpenStatusRule(       bootstrap_log )], bootstrap_log ),
        explainer       = PlainWordsExplainer(          bootstrap_log ),
        renderer        = FlaggedRowsPageRenderer(      bootstrap_log ),
        page_index      = page_index,
        logger          =                               bootstrap_log,            )


if __name__ == "__main__":
    sys.exit( build_application( sys.argv[ 1: ]).run( sys.argv[ 1: ]))

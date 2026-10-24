#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
"""ADW Gifts & Love Offerings Report -- write one HTML page listing every gift
and love offering the live non-profit books recorded for one year, with each
row's source details and a flag on anything that looks like the same gift
entered twice.

WHY THIS EXISTS

Category 190, "Gifts & Love Offerings", holds almost nothing itself -- two
rows in 2025. The giving is recorded under its subtree instead: "Individuals",
"Ministries & Organizations", each a parent of named recipients (Chosen
People, Samaritan's Purse, named people), 264 rows and $38,685.91 of it in
2025. "All Gifts and Love Offerings for 2025" therefore means the whole
subtree, which is why `--include-children` defaults ON here, unlike its
sibling workflow where rolling in sub-categories is something the caller has
to ask for.

NOT A SECOND COPY OF THE READ-ONLY STACK OR THE CATEGORY RESOLVER

`adw_interface_category_spreadsheet.py` already resolves a typed category name
to exactly one category (refusing rather than guessing when it is ambiguous),
walks a category's subtree however deep, and finds possible duplicates over a
list of rows -- against the same `ReadOnlyBooks` / `PyMySQLLiveBooks` stack
`adw_interface_live_finance.py` built first. All three are imported from that
file by location, the same trick it uses on `adw_interface_live_finance.py`,
rather than re-typed here. A second hand-written copy of "is this read-only"
or "did I find the right category" is a copy that can drift from the one an
auditor already checked.

What is new in this file: the one query that selects *source* detail columns
(source_file, receipt_url, document_url, scanned_statement_url, notes, and
which named recipient the gift was filed under) instead of the generic
category-spreadsheet's columns, the one phase that runs duplicate-detection
alone (`DuplicateCheckPhase`, rather than the sibling workflow's AnalyzePhase,
which also checks odd dates, mislabels and unassigned categories -- none of
which this report was asked to check), and the one writer that renders the
result as a styled HTML page instead of an .xlsx or .csv file.

DUPLICATE DETECTION, UNCHANGED

`DuplicateRule` is imported, not rewritten: two gifts of the same amount
within three days of each other, on the same day or sharing a word of
description, are flagged "possible duplicate" -- a thing for a person to
check, never a correction, and nothing is changed. Same reasoning as its
sibling: a human files taxes and tracks giving off these records.

Usage:
    uv run adws/adw_gifts_love_offerings_report.py "<why this report is being built>" \\
        [--year 2025] [--category "Gifts & Love Offerings"] [--category-id 190] \\
        [--no-children] [--org-type NONPROFIT] [--out /path/to/report.html] \\
        [--list-categories] [--message-out /tmp/line.txt] [--json-out /tmp/rows.json] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(connect) -> code(resolve_category)
        -> code(query) -> code(duplicate_check) -> code(write) -> code(report)
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any, Sequence

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


def _category_spreadsheet():
    """Load `adw_interface_category_spreadsheet.py` by location, once, cached.

    Same mechanism that file itself uses to reach `adw_interface_live_finance.py`:
    an ADW is a `uv run` script with a PEP 723 header, so a plain `import`
    cannot find it, and importing it only binds its definitions -- its
    `__main__` guard means nothing runs and no database connection opens yet.
    """
    cached = sys.modules.get( "adw_interface_category_spreadsheet" )
    if cached is not None:
        return cached

    path = _HERE / "adw_interface_category_spreadsheet.py"
    if not path.is_file():
        raise ImportError(
            f"Could not find { path }. This workflow reuses its category "
            "resolver, its read-only database stack and its duplicate rule "
            "rather than keeping second copies of them." )
    if str( _HERE ) not in sys.path:
        sys.path.insert( 0, str( _HERE ))
    spec = importlib.util.spec_from_file_location( "adw_interface_category_spreadsheet", path )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not build an import spec for { path }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ "adw_interface_category_spreadsheet" ] = module
    try:
        spec.loader.exec_module( module )
    except BaseException:
        del sys.modules[ "adw_interface_category_spreadsheet" ]
        raise
    return module


_cat = _category_spreadsheet()

# Borrowed whole. Nothing below re-implements any of these.
ILiveBooks          = _cat.ILiveBooks
OrgScope            = _cat.OrgScope
CategoryScope       = _cat.CategoryScope
Flag                = _cat.Flag
SheetPlan           = _cat.SheetPlan
SheetContent        = _cat.SheetContent
IFlagFinder         = _cat.IFlagFinder
ICategoryResolver   = _cat.ICategoryResolver
ReadOnlyBooks       = _cat.ReadOnlyBooks
PyMySQLLiveBooks    = _cat.PyMySQLLiveBooks
EnvFileCredentials  = _cat.EnvFileCredentials
ConnectPhase        = _cat.ConnectPhase
DEFAULT_ENV_FILE    = _cat.DEFAULT_ENV_FILE
SqlCategoryResolver = _cat.SqlCategoryResolver
NamedCategoryGroups = _cat.NamedCategoryGroups
ResolveCategoryPhase = _cat.ResolveCategoryPhase
RequestCapturePhase  = _cat.RequestCapturePhase
QueryPhase           = _cat.QueryPhase
WriteSheetPhase       = _cat.WriteSheetPhase
ReportPhase           = _cat.ReportPhase
DuplicateRule         = _cat.DuplicateRule
CompositeFlagFinder   = _cat.CompositeFlagFinder
ExpensesSection        = _cat.ExpensesSection
FlagsSection           = _cat.FlagsSection
_safe_name             = _cat._safe_name

DEFAULT_CATEGORY = "Gifts & Love Offerings"
DEFAULT_OUT_DIR  = _HERE / "adw_data" / "reports"


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class GiftsReportRequest:
    """Parsed command-line intent. Mirrors `_cat.SpreadsheetRequest`'s shape,
    duck-typed against it: `RequestCapturePhase` and `ResolveCategoryPhase`
    (both reused unchanged) only ever read `.category`, `.category_id`,
    `.include_children`, `.org_type`, `.year` and `.out_format` off whatever
    they are handed.
    """
    prompt:           str
    category:         str
    category_id:      int | None
    include_children: bool
    org_type:         str
    year:             int
    out_format:       str
    out_path:         str
    list_categories:  bool
    env_file:         str
    message_out:      str
    json_out:         str
    config:           str
    adw_id:           str | None


# -- The one new query: source details, not generic category columns --------

class GiftsExpenseRows:
    """The rows a gifts-and-love-offerings report is made of.

    Unlike the sibling workflow's generic `CategoryExpenseRows` -- built for
    any one category, so it names the category once in the report title and
    leaves it out of the row -- this report rolls together a whole subtree of
    named recipients, so which recipient a gift went to IS a per-row fact
    worth showing, alongside the source document columns this report exists
    to surface.
    """

    _COLUMNS = ( "id", "date", "amount", "method", "recipient", "merchant",
                 "description", "paid_by", "verified", "source_file",
                 "receipt_url", "document_url", "scanned_statement_url", "notes" )

    @property
    def columns( self ) -> tuple[ str, ... ]:
        return self._COLUMNS

    def fetch( self, books: ILiveBooks, scope: OrgScope, category: CategoryScope,
               years: Sequence[ int ] | None ) -> list[ dict[ str, Any ]]:
        placeholders = ", ".join( [ "%s" ] * len( category.ids ))
        wanted       = tuple( years ) if years else ()
        year_holes   = ", ".join( [ "%s" ] * len( wanted ))
        year_sql     = f" AND YEAR( e.expense_date ) IN ( { year_holes })" if wanted else ""
        year_params  = list( wanted )
        return books.fetch(
            "SELECT e.id AS id, e.expense_date AS date, e.amount AS amount, "
            "       e.method AS method, "
            "       COALESCE( c.name, '(uncategorized)' ) AS recipient, "
            "       COALESCE( m.name, '' ) AS merchant, "
            "       COALESCE( e.description, '' ) AS description, "
            "       COALESCE( p.name, '' ) AS paid_by, "
            "       IF( e.human_verified, 'yes', 'no' ) AS verified, "
            "       COALESCE( e.source_file, '' ) AS source_file, "
            "       COALESCE( e.receipt_url, '' ) AS receipt_url, "
            "       COALESCE( e.document_url, '' ) AS document_url, "
            "       COALESCE( e.scanned_statement_url, '' ) AS scanned_statement_url, "
            "       COALESCE( e.notes, '' ) AS notes "
            "FROM expenses e "
            "LEFT JOIN categories c ON c.id = e.category_id "
            "LEFT JOIN merchants  m ON m.id = e.merchant_id "
            "LEFT JOIN contacts   p ON p.id = e.paid_by_contact_id "
            f"WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM' "
            f"  AND e.category_id IN ( { placeholders })"
            + year_sql +
            " ORDER BY e.expense_date, e.id",
            [ scope.org_id, *category.ids, *year_params ],
        )


# -- The one new phase: duplicate detection alone, honestly described --------

class DuplicateCheckPhase( LifecycleLogged, IPhase ):
    """Checks the queried gifts for likely duplicates. Nothing else.

    A separate class from the sibling workflow's `AnalyzePhase` rather than a
    reuse of it: that phase's one fixed description names odd dates,
    mislabels and unassigned categories too, and running it here with only
    `DuplicateRule` installed would leave a phase whose logged description
    does not match the check it actually ran -- the exact kind of drift this
    file's reuse of everything else is trying to avoid.
    """

    def __init__( self, finder: IFlagFinder, rows: list[ dict[ str, Any ]],
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._finder = finder
        self._rows   = rows

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="duplicate_check", kind="code", owner="books",
            description="Check the queried gifts for likely duplicates -- the "
                        "same amount within a few days of another, on the same "
                        "day or sharing a word of description" )

    def act( self, handle: IPhaseHandle ) -> SheetContent:
        flags = self._finder.find( self._rows )
        handle.log( rows=len( self._rows ),
                    flagged=len({ f.expense_id for f in flags }) )
        return SheetContent( rows=self._rows, flags=flags, comparison=None )


# -- The one new writer: a styled HTML page ----------------------------------

_LINK_COLUMNS = ( "source_file", "receipt_url", "document_url", "scanned_statement_url" )

_STYLE = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { margin-bottom: 0.1rem; }
.meta { color: #555; margin-bottom: 1.5rem; }
table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; font-size: 0.9rem; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #2c3e50; color: white; position: sticky; top: 0; }
tr.total td { font-weight: bold; background: #f0f0f0; }
tr.flagged { background: #fff3cd; }
td.money { text-align: right; white-space: nowrap; }
a { color: #1a5fb4; }
"""


class HtmlSpreadsheetWriter:
    """The only new output format: one page, the Expenses table then Flags.

    Renders the same `IWorkbookSection` objects the sibling workflow's xlsx
    writer uses (`ExpensesSection`, `FlagsSection`), so the row and total
    logic -- including the TOTAL line computed from the rows written, never
    queried apart -- is shared rather than re-derived for HTML.
    """

    def __init__( self, sections: Sequence[ Any ], logger: ILifecycleLog ):
        self._sections = tuple( sections )

    @property
    def extension( self ) -> str:
        return ".html"

    def _table( self, plan: SheetPlan, content: SheetContent, section: Any ) -> str:
        lines  = section.rows( plan, content )
        money  = set( section.money_columns( plan ))
        header, *body = lines
        out = [ f"<h2>{ escape( section.title )}</h2>", "<table>", "<tr>" ]
        out += [ f"<th>{ escape( str( c ))}</th>" for c in header ]
        out.append( "</tr>" )
        for row in body:
            is_total   = bool( row ) and row[ 0 ] == "TOTAL"
            is_flagged = ( not is_total and "flags" in header and
                          row[ header.index( "flags" )] )
            css = " class=\"total\"" if is_total else ( " class=\"flagged\"" if is_flagged else "" )
            out.append( f"<tr{ css }>" )
            for index, value in enumerate( row, start=1 ):
                klass = " class=\"money\"" if index in money else ""
                column = header[ index - 1 ] if index - 1 < len( header ) else ""
                if column in _LINK_COLUMNS and value:
                    cell = f'<a href="{ escape( str( value ), quote=True )}">open</a>'
                elif index in money and isinstance( value, ( int, float )):
                    cell = f"${ Decimal( str( value )):,.2f}"
                else:
                    cell = escape( str( value )) if value not in ( "", None ) else ""
                out.append( f"<td{ klass }>{ cell }</td>" )
            out.append( "</tr>" )
        out.append( "</table>" )
        return "\n".join( out )

    def write( self, plan: SheetPlan, content: SheetContent ) -> Path:
        body = [ self._table( plan, content, section )
                 for section in self._sections if section.wanted( content )]
        html = ( f"<!doctype html><html><head><meta charset=\"utf-8\">"
                f"<title>{ escape( plan.title )}</title><style>{ _STYLE }</style></head>"
                f"<body><h1>{ escape( plan.title )}</h1>"
                f"<p class=\"meta\">{ len( content.rows )} gifts, "
                f"{ len({ f.expense_id for f in content.flags })} worth a look.</p>"
                + "\n".join( body ) + "</body></html>" )
        plan.path.parent.mkdir( parents=True, exist_ok=True )
        plan.path.write_text( html, encoding="utf-8" )
        return plan.path


# -- Argument parsing --------------------------------------------------------

class ArgparseGiftsReportArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class in this file allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> GiftsReportRequest:
        parser = argparse.ArgumentParser( description=self._description )
        parser.add_argument( "prompt", nargs="?", default="build the gifts & love offerings report",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--category", default=DEFAULT_CATEGORY,
                             help="the category name, exact or a unique fragment" )
        parser.add_argument( "--category-id", type=int, default=None,
                             help="the category's id, when the name is ambiguous" )
        parser.add_argument( "--no-children", dest="include_children",
                             action="store_false", default=True,
                             help="report only the named category itself, not its "
                                  "subtree of named recipients (on by default: the "
                                  "giving is recorded under them, not the parent)" )
        parser.add_argument( "--org-type", default="NONPROFIT",
                             help="whose books: the organizations.type to scope to" )
        parser.add_argument( "--year", type=int, default=2025,
                             help="the calendar year to report" )
        parser.add_argument( "--format", dest="out_format", default="html",
                             choices=( "html", ), help="output format (html only)" )
        parser.add_argument( "--out", dest="out_path", default="",
                             help=f"where to write it; default { DEFAULT_OUT_DIR }/<category>-<year>.html" )
        parser.add_argument( "--list-categories", action="store_true",
                             help="print the categories that have expenses, with counts, and exit" )
        parser.add_argument( "--env-file", default=DEFAULT_ENV_FILE,
                             help="file defining NON_PROFIT_USER/PASSWORD/DB_NAME when not exported" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--json-out", default="",
                             help="write the queried rows here as JSON as well" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return GiftsReportRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                                   category=parsed.category, category_id=parsed.category_id,
                                   include_children=parsed.include_children,
                                   org_type=parsed.org_type, year=parsed.year,
                                   out_format=parsed.out_format, out_path=parsed.out_path,
                                   list_categories=parsed.list_categories,
                                   env_file=parsed.env_file, message_out=parsed.message_out,
                                   json_out=parsed.json_out, config=parsed.config,
                                   adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class GiftsReportApplication( LifecycleLogged, IApplication ):
    """Orchestrates the seven-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares -- the same exception the
    shipped template itself makes.
    """

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  books:           ILiveBooks,
                  resolver:        ICategoryResolver,
                  rows_source:     GiftsExpenseRows,
                  flag_finder:     IFlagFinder,
                  writer:          HtmlSpreadsheetWriter,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._books           = books
        self._resolver        = resolver
        self._rows_source     = rows_source
        self._flag_finder     = flag_finder
        self._writer          = writer

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )

        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))

        org = self._phase_executor.execute(
            run, ConnectPhase( self._books, request.org_type, session_log ))

        if request.list_categories:
            for row in self._resolver.candidates( self._books, org, request.category ):
                print( f"{ row[ 'id' ]:>5}  { row[ 'expenses' ]:>5} expenses  "
                       f"{ Decimal( row[ 'total' ] or 0 ):>14,.2f}  { row[ 'name' ]}" )
            return run.finish( accepted=True, reason="listed the categories on file" )

        # An ambiguous or unknown category is a real answer, not a crash, and
        # it is reported rather than guessed -- same posture as the sibling
        # workflow this resolver was built for.
        try:
            category = self._phase_executor.execute(
                run, ResolveCategoryPhase( self._resolver, self._books, request, session_log ))
        except RuntimeError as refusal:
            message = f"No report was written. { refusal }"
            if request.message_out:
                Path( request.message_out ).write_text( message + "\n" )
            return run.finish( accepted=False, reason=message )

        rows = self._phase_executor.execute(
            run, QueryPhase( self._rows_source, self._books, org, category,
                             ( request.year, ), session_log ))

        content = self._phase_executor.execute(
            run, DuplicateCheckPhase( self._flag_finder, rows, session_log ))

        plan = self._plan( request, category )
        written = self._phase_executor.execute(
            run, WriteSheetPhase( self._writer, plan, content, session_log ))

        message = self._phase_executor.execute(
            run, ReportPhase( org, category, content, written, request.year, session_log ))

        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )
        if request.json_out:
            import json
            Path( request.json_out ).write_text(
                json.dumps( rows, indent=2, default=str ) + "\n" )

        return run.finish( accepted=written.exists(), reason=message )

    def _plan( self, request: GiftsReportRequest, category: CategoryScope ) -> SheetPlan:
        if request.out_path:
            path = Path( request.out_path ).expanduser()
            if path.suffix.lower() != self._writer.extension:
                path = path.with_name( path.name + self._writer.extension )
        else:
            path = DEFAULT_OUT_DIR / f"{ _safe_name( category.label )}-{ request.year }{ self._writer.extension }"
        return SheetPlan( title=f"{ category.label } ({ request.year })",
                          columns=self._rows_source.columns, path=path.resolve())


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may.

    The Decorator stack is the same one `adw_interface_live_finance.py` built
    and `adw_interface_category_spreadsheet.py` already wires: every query
    this report runs reaches MySQL only through `ReadOnlyBooks`.
    """
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    credentials = EnvFileCredentials( DEFAULT_ENV_FILE, bootstrap_log )

    expenses = ExpensesSection( bootstrap_log )
    flags    = FlagsSection( bootstrap_log )

    return GiftsReportApplication(
        argument_parser = ArgparseGiftsReportArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(             bootstrap_log ),
        session_factory = SessionRunFactory(            bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(   bootstrap_log ),
        books           = ReadOnlyBooks( PyMySQLLiveBooks( credentials, bootstrap_log ), bootstrap_log ),
        resolver        = SqlCategoryResolver( bootstrap_log, NamedCategoryGroups( bootstrap_log )),
        rows_source     = GiftsExpenseRows(),
        flag_finder     = CompositeFlagFinder([ DuplicateRule( bootstrap_log )], bootstrap_log ),
        writer          = HtmlSpreadsheetWriter([ expenses, flags ], bootstrap_log ),
        logger          =                               bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

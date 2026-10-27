#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql", "openpyxl"]
# ///
"""ADW Interface Category Spreadsheet -- write one spreadsheet of every expense
in a named category of the live non-profit books, read-only, no model anywhere
in the path.

Written in the interface style, named after `adw_interface_prompt.py`: every
collaborator is a role from `sssf_roles`, the phases are objects rather than
`with` blocks, and `build_application()` is the only function allowed to name
a concrete class. `ManualContextPhaseExecutor` drives `run.phase()`'s protocol
by hand, so there is no `with` and no `yield` here.

WHY THIS EXISTS

`adw_interface_live_finance.py` answers one question with one spoken line --
that is the right shape for a receptionist reading a figure out loud, and the
wrong shape for "send me everything we spent on Church Electric Bill". A line
cannot be opened in Excel, checked row by row against receipts, or handed to a
bookkeeper. So this workflow writes the rows themselves to a file and reports
where it put them.

Nothing here summarizes. The sheet holds the rows the SELECT returned, in the
order it returned them, plus a totals row computed from those same rows. The
one spoken line names the category, the row count, the total and the path --
every one of which is readable back off the file it just wrote.

READ-ONLY BY CONSTRUCTION, AND NOT A SECOND COPY OF IT

The MySQL account on this box holds ALL PRIVILEGES on `nonprofit_finance`, so
grants are not the safeguard; `ReadOnlyBooks` is. That Decorator, the pymysql
adapter beneath it, the env-file credentials and `ConnectPhase` are *imported
from* `adw_interface_live_finance.py` rather than copied into this file. A
second hand-written copy of a read-only guarantee is a guarantee that drifts:
the day somebody adds a keyword to one refusal list, the other workflow keeps
accepting it. One definition, two callers.

The import is by file location because an ADW is a `uv run` script with a PEP
723 header, not an importable package -- the same trick, for the same reason,
that `sssf_roles.py` uses to reach the factory's template.

AMBIGUITY IS REPORTED, NEVER GUESSED

Category names in this schema are not unique: two different rows are both
called "Other", and plenty are near-duplicates ("RM gas" / "BP Gas"). A
workflow that silently picked the first match would hand over a spreadsheet
that looks complete and is the wrong category's. So `CategoryResolverPhase`
refuses a name that matches more than one category, prints the candidates with
their ids, and names `--category-id` as the way to say which. Nothing is
written in that case -- a missing file is an obvious failure, a plausible
wrong file is not.

WHAT A ROW IS

`expense_role <> 'LINE_ITEM'` throughout, matching the live-finance questions:
a PARENT expense and its LINE_ITEMs would otherwise both be counted and the
total would be double what was actually spent. `--include-children` widens the
*category* selection (a parent category and everything filed beneath it), not
the expense roll-up -- two different trees that are easy to confuse.

WHAT ELSE IS ON THE SHEET

Rows alone answer "what did we spend on this". Two more questions always
follow, so the workbook answers them in their own tabs rather than making
somebody pivot the first one:

* `--compare` adds Year Comparison and Monthly Pattern -- this year against
  last, by sub-category and by month.
* Flags are always computed: possible duplicates, dates that cannot be right,
  rows whose description names one person and whose category belongs to
  another, and categories that do not say whose expense it is. They are
  things for a person to check, never corrections, and nothing is changed.

Both were ported from `rol_finances/adws/adw_category_spreadsheet.py`, which
this workflow supersedes -- see RETIRING THE OTHER ONE below.

FORMATS AND TABS

`xlsx` by default, because "spreadsheet" means a file somebody opens; `csv`
when the consumer is another program. Both are `ISpreadsheetWriter`, chosen in
`build_application()`, so a third format is a class and one catalog entry.

The workbook is a Composite: each tab is an `IWorkbookSection`, and
`XlsxSpreadsheetWriter` only knows how to ask a list of them to render. Adding
a tab is a class and one entry in `install_sections()`. The same shape as the
flag rules -- `IFlagRule` per kind of flag, composed by `CompositeFlagFinder`
-- for the same reason: the alternative is one function that grows a branch
every time somebody thinks of another thing worth checking.

RETIRING THE OTHER ONE

`/home/adamsl/rol_finances/adws/adw_category_spreadsheet.py` did this job
first and did parts of it better; its year comparison, its flag rules and its
"Medical" group are here now, reimplemented against these interfaces. Two
scripts answering one question out of one database is the drift this file's
import of `ReadOnlyBooks` exists to avoid, one level up. Toyota reaches this
one through `AdwCategorySpreadsheetRunner`; the older script carries a note
saying so.

Usage:
    uv run adws/adw_interface_category_spreadsheet.py "<why this is being run>" \\
        --category "Church Electric Bill" [--category-id 123] \\
        [--include-children] [--year 2025] [--org-type NONPROFIT] \\
        [--format xlsx|csv] [--out /path/to/file.xlsx] \\
        [--list-categories] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(connect) -> code(resolve_category)
        -> code(query) -> code(write) -> code(report)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
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


def _live_finance():
    """Load `adw_interface_live_finance.py` by location, once, and cache it.

    Same mechanism as `sssf_roles._load()`: an ADW is a script with a PEP 723
    header, so a plain `import` cannot find it. Importing it only binds its
    definitions -- its `__main__` guard means nothing runs.
    """
    cached = sys.modules.get( "adw_interface_live_finance" )
    if cached is not None:
        return cached

    path = _HERE / "adw_interface_live_finance.py"
    if not path.is_file():
        raise ImportError(
            f"Could not find { path }. This workflow reuses its read-only database "
            "stack rather than keeping a second copy of it."
        )
    if str( _HERE ) not in sys.path:
        sys.path.insert( 0, str( _HERE ))
    spec = importlib.util.spec_from_file_location( "adw_interface_live_finance", path )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not build an import spec for { path }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ "adw_interface_live_finance" ] = module
    try:
        spec.loader.exec_module( module )
    except BaseException:
        del sys.modules[ "adw_interface_live_finance" ]
        raise
    return module


_books_module       = _live_finance()

# The read-only stack, borrowed whole. Nothing below re-implements any of it.
ILiveBooks          = _books_module.ILiveBooks
OrgScope            = _books_module.OrgScope
EnvFileCredentials  = _books_module.EnvFileCredentials
PyMySQLLiveBooks    = _books_module.PyMySQLLiveBooks
ReadOnlyBooks       = _books_module.ReadOnlyBooks
ConnectPhase        = _books_module.ConnectPhase
DEFAULT_ENV_FILE    = _books_module.DEFAULT_ENV_FILE

DEFAULT_OUT_DIR = _HERE / "adw_data" / "spreadsheets"


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class SpreadsheetRequest:
    """Parsed command-line intent. Mirrors sssf_roles.PromptRequest's shape."""
    prompt:           str
    category:         str
    category_id:      int | None
    include_children: bool
    org_type:         str
    year:             int | None
    compare:          bool
    out_format:       str
    out_path:         str
    list_categories:  bool
    env_file:         str
    message_out:      str
    json_out:         str
    config:           str
    adw_id:           str | None


@dataclass( frozen=True )
class CategoryScope:
    """Which categories the sheet covers, and what to call them on it.

    `ids` is a list rather than one id because `--include-children` rolls a
    parent category's whole subtree into one sheet. `label` is what the file
    and the spoken line say, so both always name the same thing.
    """
    ids:      tuple[ int, ... ]
    label:    str
    children: int


@dataclass( frozen=True )
class SheetPlan:
    """Everything the writer needs that is not content."""
    title:   str
    columns: tuple[ str, ... ]
    path:    Path


@dataclass( frozen=True )
class Flag:
    """One thing on the sheet worth a person's eye. Never a correction.

    `detail` says why it was flagged, in the words that go in the cell. The
    wording matters more than it looks: "possible duplicate" invites a check,
    "duplicate" asserts a fact about somebody's bookkeeping that this
    workflow has no way to know.
    """
    expense_id: int
    on:         date | None
    description: str
    category:   str
    amount:     Decimal
    kind:       str
    detail:     str


@dataclass( frozen=True )
class CategoryComparison:
    """One sub-category, this year against last."""
    category:    str
    total:       Decimal
    count:       int
    prior_total: Decimal
    prior_count: int

    @property
    def change( self ) -> Decimal:
        return self.total - self.prior_total

    @property
    def percent( self ) -> Decimal | None:
        """None, not zero, when there is nothing to compare against.

        A category that did not exist last year has no percentage change, and
        writing 0% or 100% there would be a number somebody could act on.
        """
        if self.prior_total == 0:
            return None
        return ( self.change / self.prior_total * 100 ).quantize( Decimal( "0.1" ))


@dataclass( frozen=True )
class MonthComparison:
    """One month, this year against last."""
    month:       int
    total:       Decimal
    prior_total: Decimal


@dataclass( frozen=True )
class YearComparison:
    """A whole year against the one before it, by sub-category and by month."""
    year:        int
    by_category: tuple[ CategoryComparison, ... ]
    by_month:    tuple[ MonthComparison, ... ]

    @property
    def total( self ) -> Decimal:
        return sum(( c.total for c in self.by_category ), Decimal( 0 ))

    @property
    def prior_total( self ) -> Decimal:
        return sum(( c.prior_total for c in self.by_category ), Decimal( 0 ))


@dataclass( frozen=True )
class SheetContent:
    """Everything a writer puts in the file: the rows and what was found in them.

    One value rather than three parameters because the three are produced
    together and must stay together -- a flag list written beside a different
    query's rows would point at expense ids that are not on the sheet.
    """
    rows:       list[ dict[ str, Any ]]
    flags:      tuple[ Flag, ... ] = ()
    comparison: YearComparison | None = None


# -- Roles -------------------------------------------------------------------

class ICategoryResolver( Protocol ):
    """Turns what the caller typed into the categories a query may read.

    Separate from the questions in `adw_interface_live_finance.py` because the
    failure it has to handle is different: there, an unmatched phrase means
    "I can't answer that"; here, an ambiguous NAME means "I could answer the
    wrong one", which has to stop the run rather than pick.
    """

    def resolve( self, books: ILiveBooks, request: SpreadsheetRequest ) -> CategoryScope: ...

    def candidates( self, books: ILiveBooks, scope: OrgScope,
                    needle: str ) -> list[ dict[ str, Any ]]: ...


class IExpenseRows( Protocol ):
    """The rows a category's sheet is made of, and the columns they fill."""

    @property
    def columns( self ) -> tuple[ str, ... ]: ...

    def fetch( self, books: ILiveBooks, scope: OrgScope, category: CategoryScope,
               years: Sequence[ int ] | None ) -> list[ dict[ str, Any ]]: ...


class ICategoryGroups( Protocol ):
    """Names that stand for several category roots at once.

    "Medical" is not a row in the categories table; it is two Benefits &
    Medical parents, one per person, and everything filed under either. The
    people who ask for it do not know that and should not have to. A role
    rather than a dict literal in the resolver because the next such name is
    a different organization's, and because a group that silently stopped
    matching would look exactly like a category with no expenses.
    """

    def roots( self, name: str ) -> tuple[ int, ... ] | None: ...

    def names( self ) -> tuple[ str, ... ]: ...


class IPersonReader( Protocol ):
    """Whose expense a piece of text is about, when it says so unambiguously.

    Returns None when nobody is named, and None when more than one is: a row
    mentioning both people tells you nothing about which it belongs to, and
    guessing is how a mislabel flag gets raised against a correct record.
    """

    def person( self, text: str ) -> str | None: ...


class IFlagRule( Protocol ):
    """One kind of thing worth checking, over the whole selection.

    Whole-selection rather than per-row because the most useful rule --
    possible duplicates -- is about pairs. A rule that only saw one row at a
    time could not express it, and the interface would have to be widened
    later by whoever first tried.
    """

    @property
    def kind( self ) -> str: ...

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]: ...


class IFlagFinder( Protocol ):
    """Everything worth checking in one selection, from every installed rule."""

    def find( self, rows: list[ dict[ str, Any ]] ) -> tuple[ Flag, ... ]: ...


class IYearComparer( Protocol ):
    """This year against the one before, out of rows that span both."""

    def compare( self, rows: list[ dict[ str, Any ]], year: int ) -> YearComparison: ...


class IWorkbookSection( Protocol ):
    """One tab of the workbook. Composite: the writer only asks, never knows.

    `wanted` is asked first so a section can decline -- Year Comparison has
    nothing to draw without --compare, and an empty tab in a workbook reads
    as "there was nothing", which is a different claim from "you didn't ask".
    """

    @property
    def title( self ) -> str: ...

    def wanted( self, content: SheetContent ) -> bool: ...

    def rows( self, plan: SheetPlan, content: SheetContent ) -> list[ list[ Any ]]:
        """The tab's cells, header row first."""
        ...

    def money_columns( self, plan: SheetPlan ) -> tuple[ int, ... ]:
        """1-based columns holding money, to format as currency.

        The section names them rather than the writer guessing, because
        every tab here has a count column sitting next to a total and
        formatting one as dollars is how "12 expenses" becomes "$12.00".
        """
        ...


class ISpreadsheetWriter( Protocol ):
    """One output format. Strategy: the phase never knows which it has.

    `write` returns the path it actually wrote, because a writer is allowed to
    correct the extension it was handed -- and the spoken line must name the
    file that exists, not the one that was requested.
    """

    @property
    def extension( self ) -> str: ...

    def write( self, plan: SheetPlan, content: SheetContent ) -> Path: ...


class IWriterCatalog( Protocol ):
    """The installed formats, and which one a request selects."""

    def select( self, name: str ) -> ISpreadsheetWriter: ...

    def names( self ) -> tuple[ str, ... ]: ...


# -- Shared formatting -------------------------------------------------------

def _money( value: Any ) -> str:
    """One consistent money format, matching the live-finance workflow's."""
    return f"${ Decimal( str( value or 0 )):,.2f}"


def _safe_name( text: str ) -> str:
    """A filename that survives this schema's category names.

    Names here contain slashes ("Church Phone/Internet Bill"), em dashes and
    parentheses. Left alone they would create directories or an unopenable
    file, which would read as the query having failed.
    """
    cleaned = re.sub( r"[^A-Za-z0-9]+", "_", text ).strip( "_" )
    return ( cleaned or "category" )[ :60 ]


# Words that appear in so many descriptions here that sharing one says
# nothing about two rows being the same payment.
_STOP = { "online", "pymt", "payment", "acct", "ach", "the", "and", "health" }


def _words( text: str ) -> set[ str ]:
    """The words in a description worth comparing against another's."""
    return { w for w in re.findall( r"[a-z]{3,}", ( text or "" ).casefold() )
             if w not in _STOP }


def _on( row: dict[ str, Any ]) -> date | None:
    """The row's date as a `date`, whatever the driver handed back."""
    value = row.get( "date" )
    if isinstance( value, datetime ):
        return value.date()
    if isinstance( value, date ):
        return value
    try:
        return datetime.strptime( str( value )[ :10 ], "%Y-%m-%d" ).date()
    except ( TypeError, ValueError ):
        return None


def _amount( row: dict[ str, Any ]) -> Decimal:
    return Decimal( str( row.get( "amount" ) or 0 ))


def _flag( row: dict[ str, Any ], kind: str, detail: str ) -> Flag:
    return Flag( expense_id=int( row.get( "id" ) or 0 ), on=_on( row ),
                 description=str( row.get( "description" ) or "" ),
                 category=str( row.get( "category" ) or "" ),
                 amount=_amount( row ), kind=kind, detail=detail )


# -- Adapters ----------------------------------------------------------------

class NamedCategoryGroups( LifecycleLogged ):
    """The handful of names that mean more than one category root.

    Only "Medical" so far: ids 414 and 415 are the two Benefits & Medical
    parents, one per person, and asking for medical spending means both plus
    everything filed under them. Data rather than SQL because the grouping is
    an organization's convention, not something the schema records.
    """

    _GROUPS: dict[ str, tuple[ int, ... ]] = { "medical": ( 414, 415 ) }
    _LABELS: dict[ str, str ]              = { "medical": "Medical" }

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def roots( self, name: str ) -> tuple[ int, ... ] | None:
        return self._GROUPS.get( name.strip().lower() )

    def label( self, name: str ) -> str:
        return self._LABELS.get( name.strip().lower(), name )

    def names( self ) -> tuple[ str, ... ]:
        return tuple( self._LABELS.values() )


class InitialsPersonReader( LifecycleLogged ):
    """Whose row it is, from the initials and first names these books use.

    RJ/Robert and RM/Rosemary are the two people in this organization's
    descriptions. A row naming both is read as naming neither, because a
    shared bill is exactly the row a mislabel flag should leave alone.
    """

    _PEOPLE: dict[ str, str ] = {
        "Robert":   r"\brj\b|\(rj\)|\brobert",
        "Rosemary": r"\brm\b|\(rm\)|\brosemary",
    }

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def person( self, text: str ) -> str | None:
        lowered = ( text or "" ).casefold()
        found   = { who for who, pattern in self._PEOPLE.items()
                    if re.search( pattern, lowered )}
        return found.pop() if len( found ) == 1 else None


class SqlCategoryResolver( LifecycleLogged ):
    """Finds the category by group name, then id, then exact name, then `LIKE`.

    The steps narrow in that order so the precise thing always wins: a named
    group is explicit, an id is unambiguous, an exact name is nearly so, and
    only then is a partial match tried. More than one match at the name steps
    raises, naming the ids -- there really are two categories called "Other"
    in these books.
    """

    def __init__( self, logger: ILifecycleLog, groups: ICategoryGroups ):
        LifecycleLogged.__init__( self, logger )
        self._groups = groups

    def _subtree( self, books: ILiveBooks, roots: Sequence[ int ]) -> list[ int ]:
        """Every category under these, however deep.

        Breadth-first rather than one `WHERE parent_id IN (...)`: these books
        nest three deep in places (a parent, its person-level child, and that
        child's detail categories), and stopping at one level would quietly
        drop the rows furthest from the name that was asked for. `seen` is
        what keeps a cycle in `parent_id` from being an infinite loop rather
        than an error somebody has to diagnose.
        """
        found: list[ int ] = []
        seen               = set( roots )
        frontier           = list( roots )
        while frontier:
            holes = ", ".join( [ "%s" ] * len( frontier ))
            kids  = books.fetch(
                f"SELECT id FROM categories WHERE parent_id IN ( { holes }) ORDER BY id",
                list( frontier ))
            frontier = [ int( k[ "id" ]) for k in kids if int( k[ "id" ]) not in seen ]
            seen.update( frontier )
            found.extend( frontier )
        return found

    def resolve( self, books: ILiveBooks, request: SpreadsheetRequest ) -> CategoryScope:
        grouped = self._groups.roots( request.category ) if request.category else None
        if grouped is not None:
            ids = list( grouped ) + self._subtree( books, grouped )
            return CategoryScope( ids=tuple( ids ),
                                  label=self._groups.label( request.category ),
                                  children=len( ids ) - len( grouped ))

        if request.category_id is not None:
            rows = books.fetch(
                "SELECT id, name FROM categories WHERE id = %s", [ request.category_id ])
            if not rows:
                raise RuntimeError( f"No category has id { request.category_id }." )
            matched = rows[ 0 ]
        else:
            needle = request.category.strip()
            if not needle:
                raise RuntimeError(
                    "Name the category with --category, or its id with --category-id. "
                    "--list-categories prints what is on file." )
            rows = books.fetch(
                "SELECT id, name FROM categories WHERE name = %s ORDER BY id", [ needle ])
            if not rows:
                rows = books.fetch(
                    "SELECT id, name FROM categories WHERE name LIKE %s ORDER BY id LIMIT 25",
                    [ f"%{ needle }%" ])
            if not rows:
                raise RuntimeError(
                    f"No category matches '{ needle }'. Run --list-categories to see "
                    "what is on file." )
            if len( rows ) > 1:
                listed = "; ".join( f"{ r[ 'name' ]} (id { r[ 'id' ]})" for r in rows )
                raise RuntimeError(
                    f"'{ needle }' matches { len( rows )} categories, and picking one "
                    f"would mean writing the wrong sheet: { listed }. "
                    "Re-run with --category-id." )
            matched = rows[ 0 ]

        ids      = [ int( matched[ "id" ])]
        children = 0
        if request.include_children:
            kids = self._subtree( books, ids )
            children = len( kids )
            ids.extend( kids )

        return CategoryScope( ids=tuple( ids ), label=str( matched[ "name" ]),
                              children=children )

    def candidates( self, books: ILiveBooks, scope: OrgScope,
                    needle: str ) -> list[ dict[ str, Any ]]:
        """What `--list-categories` prints: the categories that have expenses.

        Scoped to the organization and counted, because a list of 500 names
        with no volume against them is not an answer to "which category".
        """
        like_sql    = " AND c.name LIKE %s" if needle else ""
        like_params = [ f"%{ needle }%" ] if needle else []
        return books.fetch(
            "SELECT c.id, c.name, COUNT( e.id ) AS expenses, "
            "       COALESCE( SUM( e.amount ), 0 ) AS total "
            "FROM categories c "
            "JOIN expenses e ON e.category_id = c.id AND e.org_id = %s "
            "               AND e.expense_role <> 'LINE_ITEM' "
            "WHERE 1 = 1" + like_sql +
            " GROUP BY c.id, c.name ORDER BY total DESC",
            [ scope.org_id, *like_params ],
        )


class CategoryExpenseRows( LifecycleLogged ):
    """The one SELECT behind the sheet, and the column order it fills.

    Columns are declared next to the SQL that produces them, the same reason
    `ILiveQuestion` keeps its `sql` and `speak` together: a sheet whose header
    row drifted from its SELECT would mislabel real money.
    """

    _COLUMNS = ( "id", "date", "amount", "method", "category", "merchant",
                 "description", "paid_by", "status", "verified", "receipt_url" )

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def columns( self ) -> tuple[ str, ... ]:
        return self._COLUMNS

    def fetch( self, books: ILiveBooks, scope: OrgScope, category: CategoryScope,
               years: Sequence[ int ] | None ) -> list[ dict[ str, Any ]]:
        """`years` is a list, not a year, because --compare needs two of them.

        A comparison built from a one-year query would have nothing to
        compare against and would quietly report every category as new.
        """
        placeholders = ", ".join( [ "%s" ] * len( category.ids ))
        wanted       = tuple( years ) if years else ()
        year_holes   = ", ".join( [ "%s" ] * len( wanted ))
        year_sql     = f" AND YEAR( e.expense_date ) IN ( { year_holes })" if wanted else ""
        year_params  = list( wanted )
        return books.fetch(
            "SELECT e.id AS id, e.expense_date AS date, e.amount AS amount, "
            "       e.method AS method, "
            "       COALESCE( c.name, '(uncategorized)' ) AS category, "
            "       COALESCE( m.name, '' ) AS merchant, "
            "       COALESCE( e.description, '' ) AS description, "
            "       COALESCE( p.name, '' ) AS paid_by, "
            "       e.expense_status AS status, "
            "       IF( e.human_verified, 'yes', 'no' ) AS verified, "
            "       COALESCE( e.receipt_url, '' ) AS receipt_url "
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


# -- Flag rules (one class per kind, so a fifth is a new file, not a new if) --

DUPLICATE_WINDOW_DAYS = 3


class DuplicateRule( LifecycleLogged ):
    """Two rows for the same amount within three days of each other.

    Same day, or sharing a meaningful word in the description -- a monthly
    bill paid on the 1st and the 3rd of the same month is the pattern this
    catches, and it is nearly always one payment entered twice. Nearly, not
    always, which is why the cell says "possible".
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def kind( self ) -> str:
        return "Possible duplicate"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:               # This is the main
        ordered = sorted(( r for r in rows if _on( r ) is not None ),               # engine of the whole 
                         key=lambda r: ( _on( r ), int( r.get( "id" ) or 0 )))      # Duplicate System.
        partner: dict[ int, int ] = {}
        for index, first in enumerate( ordered ):
            for second in ordered[ index + 1 : ]:
                if ( _on( second ) - _on( first )).days > DUPLICATE_WINDOW_DAYS:    # 3 days
                    break   # ordered by date, so nothing later can be nearer
                if _amount( first ) != _amount( second ):
                    continue
                same_day = _on( first ) == _on( second )
                shared   = _words( first.get( "description", "" )) & \
                           _words( second.get( "description", "" ))
                if same_day or shared:
                    a, b = int( first[ "id" ]), int( second[ "id" ])
                    partner.setdefault( a, b )
                    partner.setdefault( b, a )
        return [ _flag( r, self.kind,
                        f"Same amount near the same date as expense #{ partner[ int( r[ 'id' ])]}" )
                 for r in ordered if int( r[ "id" ]) in partner ]


class OddDateRule( LifecycleLogged ):
    """A date before 2000 or in the future: a typo, not a transaction.

    `today` is injected rather than read here so a run is repeatable -- a
    rule that consulted the clock would flag different rows tomorrow.
    """

    def __init__( self, logger: ILifecycleLog, today: date ):
        LifecycleLogged.__init__( self, logger )
        self._today = today

    @property
    def kind( self ) -> str:
        return "Odd date"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:
        found: list[ Flag ] = []
        for row in rows:
            on = _on( row )
            if on is None:
                found.append( _flag( row, self.kind, "This expense has no date" ))
            elif on.year < 2000 or on > self._today:
                found.append( _flag( row, self.kind, f"Date { on.isoformat() } looks wrong" ))
        return found


class MislabelRule( LifecycleLogged ):
    """A row whose description names one person and whose category another."""

    def __init__( self, logger: ILifecycleLog, people: IPersonReader ):
        LifecycleLogged.__init__( self, logger )
        self._people = people

    @property
    def kind( self ) -> str:
        return "Possible mislabel"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:
        found: list[ Flag ] = []
        for row in rows:
            in_name = self._people.person( str( row.get( "category" ) or "" ))
            in_text = self._people.person( str( row.get( "description" ) or "" ))
            if in_name and in_text and in_name != in_text:
                found.append( _flag(
                    row, self.kind,
                    f"Description names { in_text }, category belongs to { in_name }" ))
        return found


class UnassignedRule( LifecycleLogged ):
    """A category that says, in its own name, that nobody owns the row yet."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def kind( self ) -> str:
        return "Unassigned"

    def check( self, rows: list[ dict[ str, Any ]] ) -> list[ Flag ]:
        return [ _flag( row, self.kind, "Category does not say whose expense this is" )
                 for row in rows
                 if "unassigned" in str( row.get( "category" ) or "" ).casefold() ]


class CompositeFlagFinder( LifecycleLogged ):
    """Every installed rule, in the order they were installed.

    Composite: the phase asks one thing and gets everything, and a new rule
    is a constructor argument in `build_application` rather than an edit
    here. Rules are deliberately allowed to flag the same row twice -- a
    future-dated duplicate is two separate things to check.
    """

    def __init__( self, rules: Sequence[ IFlagRule ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._rules = tuple( rules )

    def find( self, rows: list[ dict[ str, Any ]] ) -> tuple[ Flag, ... ]:
        found: list[ Flag ] = []
        for rule in self._rules:
            found.extend( rule.check( rows ))
        return tuple( sorted( found, key=lambda f: ( f.kind, f.expense_id )))


class CategoryYearComparer( LifecycleLogged ):
    """This year beside the one before it, by sub-category and by month.

    Both years come out of the rows already on the sheet, so the comparison
    can never disagree with the Expenses tab -- which is also why the query
    must not be year-filtered when --compare is asked for.
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def compare( self, rows: list[ dict[ str, Any ]], year: int ) -> YearComparison:
        dated = [ ( r, _on( r )) for r in rows ]
        dated = [ ( r, on ) for r, on in dated if on is not None ]

        def total( chosen ) -> Decimal:
            return sum(( _amount( r ) for r in chosen ), Decimal( 0 ))

        names = sorted( { str( r.get( "category" ) or "" )
                          for r, on in dated if on.year in ( year, year - 1 )} )
        by_category = []
        for name in names:
            now    = [ r for r, on in dated
                       if str( r.get( "category" ) or "" ) == name and on.year == year ]
            before = [ r for r, on in dated
                       if str( r.get( "category" ) or "" ) == name and on.year == year - 1 ]
            by_category.append( CategoryComparison(
                category=name, total=total( now ), count=len( now ),
                prior_total=total( before ), prior_count=len( before )))

        by_month = tuple(
            MonthComparison(
                month=m,
                total=total([ r for r, on in dated if on.year == year and on.month == m ]),
                prior_total=total([ r for r, on in dated
                                    if on.year == year - 1 and on.month == m ]))
            for m in range( 1, 13 ))
        return YearComparison( year=year, by_category=tuple( by_category ),
                               by_month=by_month )


def _by_expense( flags: Sequence[ Flag ]) -> dict[ int, list[ Flag ]]:
    """Flags grouped by the row they point at, for the Expenses tab's column."""
    grouped: dict[ int, list[ Flag ]] = {}
    for flag in flags:
        grouped.setdefault( flag.expense_id, [] ).append( flag )
    return grouped


_MONTHS = ( "January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December" )


# -- Workbook sections (one tab each; the writer only asks) -------------------

class ExpensesSection( LifecycleLogged ):
    """The rows themselves, with a Flags column and a TOTAL line.

    Always wanted, even when the selection is empty: a workbook with a
    header row and no rows says "nothing is filed here", which is an answer.
    A workbook with no tab at all looks like the query never ran.
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def title( self ) -> str:
        return "Expenses"

    def wanted( self, content: SheetContent ) -> bool:
        return True

    def rows( self, plan: SheetPlan, content: SheetContent ) -> list[ list[ Any ]]:
        grouped = _by_expense( content.flags )
        out: list[ list[ Any ]] = [ list( plan.columns ) + [ "flags" ]]
        for row in content.rows:
            hits = grouped.get( int( row.get( "id" ) or 0 ), [] )
            out.append([ _cell( row.get( c ), numeric=True ) for c in plan.columns ] +
                       [ "; ".join( f"{ f.kind }: { f.detail }" for f in hits )])
        total = [ "TOTAL" ] + [ "" ] * len( plan.columns )
        for at in self.money_columns( plan ):
            total[ at - 1 ] = float( _total( content.rows ))
        out.append( total )
        return out

    def money_columns( self, plan: SheetPlan ) -> tuple[ int, ... ]:
        return ( plan.columns.index( "amount" ) + 1, ) if "amount" in plan.columns else ()


class YearComparisonSection( LifecycleLogged ):
    """Each sub-category this year beside last, with the change and the percent."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def title( self ) -> str:
        return "Year Comparison"

    def wanted( self, content: SheetContent ) -> bool:
        return content.comparison is not None

    def rows( self, plan: SheetPlan, content: SheetContent ) -> list[ list[ Any ]]:
        seen = content.comparison
        out: list[ list[ Any ]] = [
            [ "category", str( seen.year ), f"count { seen.year }",
              str( seen.year - 1 ), f"count { seen.year - 1 }", "change", "percent" ]]
        for line in seen.by_category:
            percent = line.percent
            out.append([ line.category, float( line.total ), line.count,
                         float( line.prior_total ), line.prior_count,
                         float( line.change ),
                         "" if percent is None else f"{ percent }%" ])
        out.append([ "TOTAL", float( seen.total ), "", float( seen.prior_total ), "",
                     float( seen.total - seen.prior_total ), "" ])
        return out

    def money_columns( self, plan: SheetPlan ) -> tuple[ int, ... ]:
        return ( 2, 4, 6 )


class MonthlyPatternSection( LifecycleLogged ):
    """Twelve rows, every month present whether or not anything was spent.

    All twelve on purpose: a month missing from a list reads as missing
    data, while a month showing $0.00 is the finding somebody is looking for.
    """

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def title( self ) -> str:
        return "Monthly Pattern"

    def wanted( self, content: SheetContent ) -> bool:
        return content.comparison is not None

    def rows( self, plan: SheetPlan, content: SheetContent ) -> list[ list[ Any ]]:
        seen = content.comparison
        out: list[ list[ Any ]] = [
            [ "month", str( seen.year ), str( seen.year - 1 ), "change" ]]
        for line in seen.by_month:
            out.append([ _MONTHS[ line.month - 1 ], float( line.total ),
                         float( line.prior_total ),
                         float( line.total - line.prior_total )])
        return out

    def money_columns( self, plan: SheetPlan ) -> tuple[ int, ... ]:
        return ( 2, 3, 4 )


class FlagsSection( LifecycleLogged ):
    """Everything the rules found, one row each, with the id to look up."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def title( self ) -> str:
        return "Flags"

    def wanted( self, content: SheetContent ) -> bool:
        return bool( content.flags )

    def rows( self, plan: SheetPlan, content: SheetContent ) -> list[ list[ Any ]]:
        out: list[ list[ Any ]] = [
            [ "kind", "expense id", "date", "amount", "category",
              "description", "why it was flagged" ]]
        for flag in content.flags:
            out.append([ flag.kind, flag.expense_id,
                         flag.on.isoformat() if flag.on else "",
                         float( flag.amount ), flag.category,
                         flag.description, flag.detail ])
        return out

    def money_columns( self, plan: SheetPlan ) -> tuple[ int, ... ]:
        return ( 4, )


# -- Writers ------------------------------------------------------------------

class CsvSpreadsheetWriter( LifecycleLogged ):
    """Plain CSV, for when the next reader is a program.

    One file means one table, so this writes the Expenses section only and
    says so in the run's log rather than silently dropping the comparison
    somebody asked for. Anyone wanting the other tabs wants xlsx.
    """

    def __init__( self, section: IWorkbookSection, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._section = section

    @property
    def extension( self ) -> str:
        return ".csv"

    def write( self, plan: SheetPlan, content: SheetContent ) -> Path:
        import csv

        plan.path.parent.mkdir( parents=True, exist_ok=True )
        with plan.path.open( "w", newline="", encoding="utf-8" ) as handle:
            writer = csv.writer( handle )
            for line in self._section.rows( plan, content ):
                writer.writerow( line )
        return plan.path


class XlsxSpreadsheetWriter( LifecycleLogged ):
    """The default: a real workbook, one tab per section that wants one.

    The only class here that knows openpyxl exists, and it knows nothing
    about what any tab means -- it asks each section whether it is wanted,
    then for its cells. Amounts are written as numbers with a currency
    format rather than as pre-formatted strings, so every total the file
    carries can be re-derived by whoever opens it; a sheet whose numbers are
    text is a sheet nobody can check.
    """

    def __init__( self, sections: Sequence[ IWorkbookSection ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        if not sections:
            raise ValueError( "A workbook needs at least one section." )
        self._sections = tuple( sections )

    @property
    def extension( self ) -> str:
        return ".xlsx"

    def _tab_name( self, title: str ) -> str:
        # Excel refuses >31 chars and the five characters below in a tab name.
        return re.sub( r"[\[\]:*?/\\]", "-", title )[ :31 ] or "Sheet"

    def write( self, plan: SheetPlan, content: SheetContent ) -> Path:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter

        book  = Workbook()
        first = True
        for section in self._sections:
            if not section.wanted( content ):
                continue
            sheet = book.active if first else book.create_sheet()
            first = False
            sheet.title = self._tab_name(
                plan.title if section.title == "Expenses" else section.title )

            lines = section.rows( plan, content )
            for line in lines:
                sheet.append( line )

            for cell in sheet[ 1 ]:
                cell.font      = Font( bold=True )
                cell.alignment = Alignment( horizontal="left" )
            if sheet.max_row > 1 and lines and lines[ -1 ][ 0 ] == "TOTAL":
                for cell in sheet[ sheet.max_row ]:
                    cell.font = Font( bold=True )

            for column in section.money_columns( plan ):
                for index in range( 2, sheet.max_row + 1 ):
                    sheet.cell( row=index, column=column ).number_format = '"$"#,##0.00'

            sheet.freeze_panes = "A2"
            for index in range( 1, sheet.max_column + 1 ):
                widest = max( len( str( sheet.cell( row=r, column=index ).value or "" ))
                              for r in range( 1, min( sheet.max_row, 500 ) + 1 ))
                sheet.column_dimensions[ get_column_letter( index )].width = \
                    min( 48, widest + 2 )

        plan.path.parent.mkdir( parents=True, exist_ok=True )
        book.save( plan.path )
        return plan.path


class WriterCatalog( LifecycleLogged ):
    """The installed formats. A new one is a class plus an entry here."""

    def __init__( self, writers: dict[ str, ISpreadsheetWriter ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        if not writers:
            raise ValueError( "At least one spreadsheet writer must be installed." )
        self._writers = dict( writers )

    def select( self, name: str ) -> ISpreadsheetWriter:
        try:
            return self._writers[ name.strip().lower().lstrip( "." )]
        except KeyError:
            raise RuntimeError(
                f"Unknown format '{ name }'. Installed: { ', '.join( self.names())}."
            ) from None

    def names( self ) -> tuple[ str, ... ]:
        return tuple( self._writers )


def _cell( value: Any, numeric: bool = False ) -> Any:
    """One conversion both writers share, so the two files agree cell for cell."""
    if value is None:
        return ""
    if isinstance( value, Decimal ):
        return float( value ) if numeric else f"{ value:.2f}"
    if isinstance( value, datetime ):
        return value.strftime( "%Y-%m-%d %H:%M:%S" )
    if isinstance( value, date ):
        return value.isoformat()
    return value


def _total( rows: list[ dict[ str, Any ]]) -> Decimal:
    """The totals row, computed from the rows written -- never queried apart.

    A separate SUM() query could disagree with the rows on the sheet the
    moment a filter changed in one place and not the other, and the sheet
    would be the thing people trusted.
    """
    return sum(( Decimal( str( row.get( "amount" ) or 0 )) for row in rows ), Decimal( "0" ))


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records which category's sheet is being asked for, nothing more."""

    def __init__( self, engineer: str, request: SpreadsheetRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which category's spreadsheet is being asked for, and the org, year and format it is scoped to" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt,
                    category=r.category or ( f"id { r.category_id }" if r.category_id else "(none)" ),
                    org_type=r.org_type, year=r.year if r.year is not None else "(all)",
                    include_children=r.include_children, format=r.out_format )


class ResolveCategoryPhase( LifecycleLogged, IPhase ):
    """
        # Resolve Category Phase

    **Purpose:** Find and verify the requested category.

    ## How It Works
    1. Read the requested category.
    2. Find the matching database category.
    3. Reject unclear matches. Never guess.
    4. Collect the matching IDs.
    5. Log the results.
    6. Return the verified category information.

    ## Dependencies
    - **resolver:** Finds and verifies categories.
    - **books:** Provides financial records.
    - **request:** Contains the user's request.
    - **logger:** Records phase activity.

    ## Returns
    **CategoryScope:** Verified category, IDs, and children.

    ## Design
    - Program to interfaces.
    - Keep responsibilities separate.
    - Delegate matching to ICategoryResolver. """

    def __init__( self, resolver: ICategoryResolver, books: ILiveBooks,
                  request: SpreadsheetRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._resolver = resolver
        self._books    = books
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="resolve_category", kind="code", owner="books",
            description="Resolve the typed category to exactly one category on file, refusing rather than guessing when the name is ambiguous" )

    def act( self, handle: IPhaseHandle ) -> CategoryScope:
        scope = self._resolver.resolve( self._books, self._request )
        handle.log( category=scope.label, ids=", ".join( str( i ) for i in scope.ids ),
                    children=scope.children )
        return scope


class QueryPhase( LifecycleLogged, IPhase ):
    """Runs the one read-only SELECT behind the sheet."""

    def __init__( self, rows: IExpenseRows, books: ILiveBooks, org: OrgScope,
                  category: CategoryScope, years: Sequence[ int ] | None,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._rows     = rows
        self._books    = books
        self._org      = org
        self._category = category
        self._years    = years

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="query", kind="code", owner="books",
            description="Run the category's single read-only SELECT against the live books and keep every row it returns" )

    def act( self, handle: IPhaseHandle ) -> list[ dict[ str, Any ]]:
        rows = self._rows.fetch( self._books, self._org, self._category, self._years )
        handle.log( rows=len( rows ), total=_money( _total( rows )))
        return rows


class AnalyzePhase( LifecycleLogged, IPhase ):
    """Reads the rows for things worth a person's eye, and for last year.

    Reads only. Nothing here changes a row, and nothing here decides a row
    is wrong -- it decides a row is worth checking, which is a different
    claim and the only one this workflow is entitled to make.
    """

    def __init__( self, finder: IFlagFinder, comparer: IYearComparer,
                  rows: list[ dict[ str, Any ]], year: int | None, compare: bool,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._finder   = finder
        self._comparer = comparer
        self._rows     = rows
        self._year     = year
        self._compare  = compare

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="analyze", kind="code", owner="books",
                            description="Check the queried rows for possible duplicates, odd dates, mislabels and unassigned categories, and compare the year against the one before it when asked" )

    def act( self, handle: IPhaseHandle ) -> SheetContent:
        comparison = ( self._comparer.compare( self._rows, self._year )
                       if self._compare and self._year is not None else None )

        # --compare widened the query to last year as well, and those rows
        # have done their job once the comparison is built. They are dropped
        # here so the Expenses tab, its TOTAL, and the spoken line all mean
        # the year that was asked for -- a sheet headed 2025 whose total
        # quietly included 2024 is the exact kind of wrong figure this
        # workflow exists to avoid.
        rows = ( [ r for r in self._rows
                   if ( _on( r ) is not None and _on( r ).year == self._year )]
                 if comparison is not None else self._rows )

        flags = self._finder.find( rows )
        kinds = sorted( { f.kind for f in flags } )
        handle.log( rows=len( rows ), flags=len( flags ),
                    kinds=", ".join( kinds ) or "(none)",
                    compared=self._year if comparison else "(not asked)" )
        return SheetContent( rows=rows, flags=flags, comparison=comparison )


class WriteSheetPhase( LifecycleLogged, IPhase ):
    """Hands the content to whichever writer the request chose."""

    def __init__( self, writer: ISpreadsheetWriter, plan: SheetPlan,
                  content: SheetContent, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._writer  = writer
        self._plan    = plan
        self._content = content
        self._rows    = content.rows

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="write", kind="code", owner="books",
                description="Write the queried rows and their total to the spreadsheet file, in the chosen format" )

    def act( self, handle: IPhaseHandle ) -> Path:
        written = self._writer.write( self._plan, self._content )
        handle.log( path=str( written ), rows=len( self._rows ),
                    bytes=written.stat().st_size if written.exists() else 0 )
        return written


class ReportPhase( LifecycleLogged, IPhase ):
    """The one line, every part of which is readable back off the file."""

    def __init__( self, org: OrgScope, category: CategoryScope,
                  content: SheetContent, written: Path, year: int | None,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._org      = org
        self._category = category
        self._content  = content
        self._rows     = content.rows
        self._written  = written
        self._year     = year

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="books",
                            description="State the category, the row count, the total and the path of the spreadsheet that was written" )

    def act( self, handle: IPhaseHandle ) -> str:
        when     = f" in { self._year }" if self._year is not None else ""
        rolled   = ( f" (and { self._category.children } sub-categories)"
                     if self._category.children else "" )
        if not self._rows:
            message = ( f"{ self._org.name }: { self._category.label }{ rolled } has no "
                        f"expenses{ when }, so the sheet at { self._written } has headers "
                        "and no rows." )
        else:
            message = ( f"{ self._org.name }: wrote { len( self._rows )} "
                        f"{ self._category.label }{ rolled } expenses{ when } totalling "
                        f"{ _money( _total( self._rows ))} to { self._written }." )
            # Said in the same breath as the total, because a count of rows
            # worth checking is useless to somebody who has already closed
            # the file -- and the spoken line is all a receptionist repeats.
            if self._content.flags:
                rows_flagged = len( { f.expense_id for f in self._content.flags } )
                # Named off the file that was actually written: a CSV has one
                # table with a flags column and no tabs at all, and telling
                # somebody to open a tab that is not there reads as the
                # workflow having written the wrong file.
                where = ( "the Flags tab says why" if self._written.suffix == ".xlsx"
                          else "the flags column says why" )
                message += f" { rows_flagged } of them are worth a look; { where }."
            if self._content.comparison is not None:
                seen = self._content.comparison
                message += ( f" Against { seen.year - 1 }'s "
                             f"{ _money( seen.prior_total )}, that is a change of "
                             f"{ _money( seen.total - seen.prior_total )}." )
        handle.log( message=message )
        return message


# -- Argument parsing --------------------------------------------------------

class ArgparseCategorySpreadsheetArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, formats: tuple[ str, ... ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description
        self._formats     = formats

    def parse( self, argv: list[ str ]) -> SpreadsheetRequest:
        parser = argparse.ArgumentParser( description=self._description )
        parser.add_argument( "prompt", nargs="?", default="build a category spreadsheet",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--category", default="",
                             help="the category name, exact or a unique fragment" )
        parser.add_argument( "--category-id", type=int, default=None,
                             help="the category's id, when the name is ambiguous" )
        parser.add_argument( "--include-children", action="store_true",
                             help="roll sub-categories of the matched category into the same sheet" )
        parser.add_argument( "--org-type", default="NONPROFIT",
                             help="whose books: the organizations.type to scope to" )
        parser.add_argument( "--year", type=int, default=None,
                             help="restrict to one calendar year" )
        parser.add_argument( "--compare", action="store_true",
                             help="add Year Comparison and Monthly Pattern tabs against the prior year; needs --year" )
        parser.add_argument( "--format", dest="out_format", default="xlsx",
                             choices=self._formats, help="output format" )
        parser.add_argument( "--out", dest="out_path", default="",
                             help=f"where to write it; default { DEFAULT_OUT_DIR }/<category>[-<year>].<ext>" )
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
        return SpreadsheetRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                                   category=parsed.category, category_id=parsed.category_id,
                                   include_children=parsed.include_children,
                                   org_type=parsed.org_type, year=parsed.year,
                                   compare=parsed.compare,
                                   out_format=parsed.out_format, out_path=parsed.out_path,
                                   list_categories=parsed.list_categories,
                                   env_file=parsed.env_file, message_out=parsed.message_out,
                                   json_out=parsed.json_out, config=parsed.config,
                                   adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class CategorySpreadsheetApplication( LifecycleLogged, IApplication ):
    """Orchestrates the six-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares -- the same exception the
    shipped template itself makes (`IPhaseExecutor.execute(run: Run, ...)`).
    """

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  books:           ILiveBooks,
                  resolver:        ICategoryResolver,
                  rows_source:     IExpenseRows,
                  flag_finder:     IFlagFinder,
                  year_comparer:   IYearComparer,
                  writers:         IWriterCatalog,
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
        self._year_comparer   = year_comparer
        self._writers         = writers

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        writer  = self._writers.select( request.out_format )

        # Refused before anything connects, because "compared against what?"
        # has no safe default: picking this year silently would answer a
        # question about a year nobody named.
        if request.compare and request.year is None:
            message = ( "No spreadsheet was written. --compare needs --year, so the "
                        "sheet knows which year it is comparing against." )
            if request.message_out:
                Path( request.message_out ).write_text( message + "\n" )
            print( message )
            return 1

        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))

        org = self._phase_executor.execute(
            run, ConnectPhase( self._books, request.org_type, session_log ))

        # Listing is a question about the books, not a spreadsheet, so it
        # answers after the scope is known and before anything is written.
        if request.list_categories:
            for row in self._resolver.candidates( self._books, org, request.category ):
                print( f"{ row[ 'id' ]:>5}  { row[ 'expenses' ]:>5} expenses  "
                       f"{ _money( row[ 'total' ]):>14}  { row[ 'name' ]}" )
            return run.finish( accepted=True, reason="listed the categories on file" )

        # A category that cannot be resolved -- missing, unknown, or matching
        # more than one row -- is a real answer, not a crash. The caller may be
        # a receptionist runner reading --message-out, and a traceback there
        # would be reported as the workflow having broken rather than as the
        # question having been ambiguous. Nothing is written in this case.
        try:
            category = self._phase_executor.execute(
                run, ResolveCategoryPhase( self._resolver, self._books, request, session_log ))
        except RuntimeError as refusal:
            message = f"No spreadsheet was written. { refusal }"
            if request.message_out:
                Path( request.message_out ).write_text( message + "\n" )
            return run.finish( accepted=False, reason=message )

        # --compare widens the query to two years rather than running a
        # second one, so the comparison and the Expenses tab are the same
        # rows and cannot disagree about a total.
        years = None
        if request.year is not None:
            years = ( request.year, request.year - 1 ) if request.compare else ( request.year, )

        rows = self._phase_executor.execute(
            run, QueryPhase( self._rows_source, self._books, org, category,
                             years, session_log ))

        content = self._phase_executor.execute(
            run, AnalyzePhase( self._flag_finder, self._year_comparer, rows,
                               request.year, request.compare, session_log ))

        plan    = self._plan( request, category, writer )
        written = self._phase_executor.execute(
            run, WriteSheetPhase( writer, plan, content, session_log ))

        message = self._phase_executor.execute(
            run, ReportPhase( org, category, content, written, request.year, session_log ))

        # The caller may be a receptionist runner rather than a terminal. It
        # reads this file, so the operator's line is a contract -- same rule as
        # adw_interface_live_finance.py and adw_interface_browser_open.py.
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )
        if request.json_out:
            Path( request.json_out ).write_text(
                json.dumps( rows, indent=2, default=str ) + "\n" )

        # An empty category is a true answer and is accepted as one: the sheet
        # exists and says so. What is NOT accepted is the file not being there.
        return run.finish( accepted=written.exists(), reason=message )

    def _plan( self, request: SpreadsheetRequest, category: CategoryScope,
               writer: ISpreadsheetWriter ) -> SheetPlan:
        """Where the file goes and what it is called.

        A `--out` without the writer's extension gets it appended rather than
        swapped: honouring a typed name matters more than tidiness, and the
        spoken line names whatever is actually on disk either way.
        """
        if request.out_path:
            path = Path( request.out_path ).expanduser()
            if path.suffix.lower() != writer.extension:
                path = path.with_name( path.name + writer.extension )
        else:
            year = f"-{ request.year }" if request.year is not None else ""
            path = DEFAULT_OUT_DIR / f"{ _safe_name( category.label )}{ year }{ writer.extension }"
        return SheetPlan( title=category.label, columns=self._rows_source.columns,
                          path=path.resolve())


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may.

    The Decorator stack is the same one `adw_interface_live_finance.py` builds,
    assembled here from the same classes: every query this workflow runs
    reaches MySQL only through `ReadOnlyBooks`.
    """
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    credentials = EnvFileCredentials( DEFAULT_ENV_FILE, bootstrap_log )
    people      = InitialsPersonReader( bootstrap_log )

    # The Expenses section is shared: the CSV writer writes that one table,
    # the workbook writer leads with it. One object so the two formats can
    # never disagree about what a row looks like.
    expenses = ExpensesSection( bootstrap_log )
    writers  = WriterCatalog(
        { "xlsx": XlsxSpreadsheetWriter([ expenses,
                                          YearComparisonSection( bootstrap_log ),
                                          MonthlyPatternSection( bootstrap_log ),
                                          FlagsSection(          bootstrap_log )],
                                        bootstrap_log ),
          "csv":  CsvSpreadsheetWriter( expenses, bootstrap_log )}, bootstrap_log )
    return CategorySpreadsheetApplication(
        argument_parser = ArgparseCategorySpreadsheetArgumentParser(
                              __doc__, writers.names(), bootstrap_log ),   # type: ignore
        config_loader   = SssfConfigLoader(             bootstrap_log ),
        session_factory = SessionRunFactory(            bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(   bootstrap_log ),
        books           = ReadOnlyBooks( PyMySQLLiveBooks( credentials, bootstrap_log ),    bootstrap_log ),
        resolver        = SqlCategoryResolver(          bootstrap_log, NamedCategoryGroups( bootstrap_log )),
        rows_source     = CategoryExpenseRows(          bootstrap_log ),
        flag_finder     = CompositeFlagFinder([ DuplicateRule(  bootstrap_log ),
                                                OddDateRule(    bootstrap_log,
                                                                date.today()),
                                                MislabelRule(   bootstrap_log, people ),
                                                UnassignedRule( bootstrap_log )], bootstrap_log ),
        year_comparer   = CategoryYearComparer(         bootstrap_log ),
        writers         = writers,
        logger          =                               bootstrap_log,                          )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

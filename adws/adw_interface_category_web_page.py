#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql", "openpyxl"]
# ///
"""ADW Interface Category Web Page -- write one self-contained HTML page with a
table of every expense in a named category of the live non-profit books,
read-only, no model anywhere in the path.

Written in the interface style, like its sibling
`adw_interface_category_spreadsheet.py`: every collaborator is a role from
`sssf_roles`, the phases are objects rather than `with` blocks, and
`build_application()` is the only function allowed to name a concrete class.

WHY THIS EXISTS

The spreadsheet ADW answers "send me everything we spent on X" with a file
somebody opens in Excel. A web page answers the same question for somebody
who wants to look, not edit: one file, opens in any browser, nothing to
install. Same rows, same total, different reader.

NOT A SECOND COPY OF ANYTHING

The read-only database stack (`ReadOnlyBooks` and friends), the category
resolver that refuses an ambiguous name, and the one SELECT that defines what
a row is are all *imported from* `adw_interface_category_spreadsheet.py`, which
in turn imports the database stack from `adw_interface_live_finance.py`. So a
page and a spreadsheet of the same category and year are the same rows by
construction -- they cannot disagree about a total, because there is only one
query. The only new thing here is `HtmlTablePageRenderer`.

The import is by file location for the same reason the sibling's is: an ADW
is a `uv run` script with a PEP 723 header, not an importable package.

ESCAPING

Every value that reaches the page goes through `html.escape`, and a receipt
URL is only made a link when it is http(s). Descriptions in these books are
typed by people and pasted from bank exports; a page that rendered one as
markup would be showing something other than the record.

THE ADW GENERATED LIST

Every page is also linked from `index.html` in the directory it was written
to -- by default `adw_data/web_pages/`, which the dashboard serves as
ROL Finance -> Reports -> ADW Generated. The newest link goes on top and the
older ones move down. The list is rebuilt from `index.json` beside it rather
than by editing the old HTML, so a hand-edited or half-written index can
never lose a link.

THE DASHBOARD MAY BE ON ANOTHER MACHINE

The dashboard serving that list runs on one host (`DEFAULT_DASHBOARD_HOST`);
`localhost:8765` elsewhere is only a forwarder to it. A run on any other
machine used to update that machine's list, which nothing displays.
`DashboardHostLinkIndex` closes the gap: after the local list is updated it
copies the page to the dashboard host and puts it on top of *that* host's
list, merged with whatever is already there. On the dashboard host itself
it does nothing extra. It never fails the run -- the page is already
written -- but the report line says whether the dashboard got it.
`--dashboard-host none` turns it off.

A page's default name carries the moment it was written --
`<category>_<Month>_<day>_hh_mm_ss.html` -- so a second run never
overwrites the first and every link stays pointing at what it said.

Usage:
    uv run adws/adw_interface_category_web_page.py "<why this is being run>" \\
        --category "Gifts and Love Offerings" --year 2025 \\
        [--category-id 123] [--no-include-children] [--org-type NONPROFIT] \\
        [--out /path/to/page.html] [--message-out /tmp/line.txt] \\
        [--dashboard-host user@host | none] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(connect) -> code(resolve_category)
        -> code(query) -> code(render) -> code(publish) -> code(report)
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

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
    """Load `adw_interface_category_spreadsheet.py` by location, once, and cache it.

    Same mechanism as that file's own `_live_finance()`. Importing it only
    binds its definitions -- its `__main__` guard means nothing runs.
    """
    cached = sys.modules.get( "adw_interface_category_spreadsheet" )
    if cached is not None:
        return cached

    path = _HERE / "adw_interface_category_spreadsheet.py"
    if not path.is_file():
        raise ImportError(
            f"Could not find { path }. This workflow reuses its category resolver "
            "and expense query rather than keeping a second copy of them."
        )
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


_sheet_module         = _category_spreadsheet()

# Borrowed whole. Nothing below re-implements any of it.
ILiveBooks            = _sheet_module.ILiveBooks
OrgScope              = _sheet_module.OrgScope
EnvFileCredentials    = _sheet_module.EnvFileCredentials
PyMySQLLiveBooks      = _sheet_module.PyMySQLLiveBooks
ReadOnlyBooks         = _sheet_module.ReadOnlyBooks
ConnectPhase          = _sheet_module.ConnectPhase
DEFAULT_ENV_FILE      = _sheet_module.DEFAULT_ENV_FILE
CategoryScope         = _sheet_module.CategoryScope
ICategoryResolver     = _sheet_module.ICategoryResolver
IExpenseRows          = _sheet_module.IExpenseRows
SqlCategoryResolver   = _sheet_module.SqlCategoryResolver
NamedCategoryGroups   = _sheet_module.NamedCategoryGroups
CategoryExpenseRows   = _sheet_module.CategoryExpenseRows
ResolveCategoryPhase  = _sheet_module.ResolveCategoryPhase
QueryPhase            = _sheet_module.QueryPhase
_money                = _sheet_module._money
_total                = _sheet_module._total
_safe_name            = _sheet_module._safe_name
_cell                 = _sheet_module._cell

DEFAULT_OUT_DIR = _HERE / "adw_data" / "web_pages"
INDEX_PAGE      = "index.html"
INDEX_MANIFEST  = "index.json"

# Where the dashboard showing ROL Finance -> Reports -> ADW Generated runs,
# and its copy of DEFAULT_OUT_DIR (relative to that account's home).
DEFAULT_DASHBOARD_HOST = "adamsl@100.102.209.100"
DASHBOARD_DIR          = "agent_blocks/adws/adw_data/web_pages"


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class PageRequest:
    """Parsed command-line intent.

    `category`, `category_id` and `include_children` carry the same names as
    the sibling's `SpreadsheetRequest` on purpose: they are the three fields
    `SqlCategoryResolver.resolve()` reads, so the borrowed resolver takes this
    request unchanged.
    """
    prompt:           str
    category:         str
    category_id:      int | None
    include_children: bool
    org_type:         str
    year:             int | None
    out_path:         str
    env_file:         str
    message_out:      str
    config:           str
    adw_id:           str | None


@dataclass( frozen=True )
class PagePlan:
    """Everything the renderer needs that is not rows."""
    org:      str
    category: CategoryScope
    year:     int | None
    columns:  tuple[ str, ... ]
    adw_id:   str
    path:     Path


@dataclass( frozen=True )
class PublishedLink:
    """Where the list now is, and anything the operator should hear about it."""
    index: Path
    note:  str = ""


@dataclass( frozen=True )
class PageLink:
    """One entry on the ADW Generated list. `file` is relative to the list."""
    file:      str
    summary:   str
    generated: str


# -- Roles -------------------------------------------------------------------

class IPageRenderer( Protocol ):
    """One page format. Returns the path it actually wrote."""

    def render( self, plan: PagePlan, rows: list[ dict[ str, Any ]] ) -> Path: ...


class IPageIndex( Protocol ):
    """The list of written pages. Puts `page` on top; says where the list is."""

    def publish( self, page: Path, summary: str ) -> PublishedLink: ...


# -- Adapters ----------------------------------------------------------------

class HtmlTablePageRenderer( LifecycleLogged ):
    """A single self-contained HTML file: a summary, the table, a TOTAL row.

    No external scripts, stylesheets or fonts to fetch -- the page has to
    open the same way from a file path, an email attachment, or a phone. The
    one inline script only positions the status pop-up; with scripts off the
    flagged rows are still yellow. The TOTAL is computed from the rows on the
    page (`_total`), never queried apart, for the same reason the
    spreadsheet's is.
    """

    # Header labels for the columns `CategoryExpenseRows` returns. A column
    # missing here falls back to its raw name rather than vanishing.
    _LABELS: dict[ str, str ] = {
        "id": "ID", "date": "Date", "amount": "Amount",
        "category": "Category", "merchant": "Merchant", "description": "Description",
        "receipt_url": "Receipt",
    }

    # Queried (the rows source is shared with the spreadsheet) but not shown.
    # `status` still drives the row highlight and its pop-up.
    _HIDDEN = frozenset({ "method", "paid_by", "status", "verified" })

    # `expenses.expense_status`, in words. NONE (or empty) means no status.
    _STATUS_TEXT: dict[ str, str ] = {
        "NEEDS_VENDOR_KEY":
            "Needs vendor key: the importer did not recognise this vendor, so the "
            "expense is waiting for someone to map the vendor and confirm its "
            "category in the dashboard.",
        "WAITING_FOR_PAYMENT_COUNTERPART":
            "Waiting for payment counterpart: this row was recorded from an invoice "
            "or bill, and the matching bank or card payment has not been linked to it yet.",
        "COUNTERPART_DOCUMENT_LINKED":
            "Counterpart document linked: this payment has been matched to its "
            "invoice or receipt document.",
    }

    _STYLE = """
:root { --bg: #ffffff; --fg: #1d2330; --muted: #5d6676; --line: #dfe3ea;
        --stripe: #f6f8fb; --head: #eef1f6; --accent: #2f5fa7;
        --flag: #fff3a3; --flag-hover: #ffe866; --tip-bg: #1d2330; --tip-fg: #ffffff; }
@media ( prefers-color-scheme: dark ) {
  :root { --bg: #14171d; --fg: #e4e8ef; --muted: #9aa3b2; --line: #2c323d;
          --stripe: #1a1e26; --head: #222833; --accent: #8db4f0;
          --flag: #4a4212; --flag-hover: #5c5216; --tip-bg: #f4f6fa; --tip-fg: #14171d; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: var( --bg ); color: var( --fg );
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
.sub { color: var( --muted ); margin: 0 0 20px; }
.stats { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
.stat { border: 1px solid var( --line ); border-radius: 8px; padding: 10px 14px; min-width: 140px; }
.stat b { display: block; font-size: 1.25rem; }
.stat span { color: var( --muted ); font-size: .85rem; }
.wrap { overflow-x: auto; border: 1px solid var( --line ); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 8px 10px; border-bottom: 1px solid var( --line ); text-align: left; vertical-align: top; }
thead th { background: var( --head ); position: sticky; top: 0; white-space: nowrap; }
tbody tr:nth-child( even ) { background: var( --stripe ); }
tbody tr.flagged { background: var( --flag ); cursor: help; }
tbody tr.flagged:hover { background: var( --flag-hover ); }
.flag-key { display: inline-block; width: .9em; height: .9em; border-radius: 3px;
            background: var( --flag ); border: 1px solid var( --line ); vertical-align: -1px; }
.tip { position: fixed; z-index: 10; max-width: 340px; padding: 8px 11px; border-radius: 6px;
       background: var( --tip-bg ); color: var( --tip-fg ); font-size: .85rem;
       box-shadow: 0 4px 14px rgba( 0, 0, 0, .25 ); pointer-events: none; }
.tip[hidden] { display: none; }
td.num, th.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
td.date { white-space: nowrap; }
tfoot td { font-weight: 700; border-top: 2px solid var( --line ); border-bottom: 0; }
a { color: var( --accent ); }
.empty { padding: 24px; color: var( --muted ); }
footer { color: var( --muted ); font-size: .8rem; margin-top: 16px; }
"""

    # Shows a flagged row's `data-status` beside the pointer; hides on leave.
    _SCRIPT = """
( () => {
  const tip = document.getElementById( "tip" );
  const place = ( e ) => {
    let x = e.clientX + 14, y = e.clientY + 18;
    if ( x + tip.offsetWidth > innerWidth - 8 ) x = innerWidth - tip.offsetWidth - 8;
    if ( y + tip.offsetHeight > innerHeight - 8 ) y = e.clientY - tip.offsetHeight - 12;
    tip.style.left = Math.max( 8, x ) + "px";
    tip.style.top  = Math.max( 8, y ) + "px";
  };
  for ( const row of document.querySelectorAll( "tr[data-status]" )) {
    row.addEventListener( "mouseenter", ( e ) => { tip.textContent = row.dataset.status; tip.hidden = false; place( e ); });
    row.addEventListener( "mousemove", place );
    row.addEventListener( "mouseleave", () => { tip.hidden = true; });
  }
})();
"""

    # The page's own words, as attributes so a page built on these rows for
    # another purpose (the duplicate check) can say what *it* is.
    _KEY_WORDS  = "with a status -- hover a yellow row to see it."
    _EMPTY_TEXT = "No expenses are filed in this category for {when}."
    _MADE_BY    = "Read-only from the live books. Generated {generated} by\nadw_interface_category_web_page.py, run {adw_id}."

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def _value( self, column: str, value: Any ) -> str:
        """One cell's HTML. Escaped always; a link only for an http(s) receipt."""
        if column == "amount":
            return html.escape( _money( value ))
        text = str( _cell( value ))
        if column == "receipt_url" and text:
            if text.lower().startswith(( "http://", "https://" )):
                return f'<a href="{ html.escape( text, quote=True )}">view</a>'
        return html.escape( text )

    def _class( self, column: str ) -> str:
        if column == "amount":
            return ' class="num"'
        if column == "date":
            return ' class="date"'
        return ""

    def _status( self, row: dict[ str, Any ] ) -> str:
        """The row's status in words, or "" when it has none."""
        code = str( row.get( "status" ) or "" ).strip()
        if not code or code.upper() == "NONE":
            return ""
        return self._STATUS_TEXT.get( code, f"Status: { code }" )

    def _row_open( self, row: dict[ str, Any ] ) -> str:
        status = self._status( row )
        if not status:
            return "<tr>"
        return f'<tr class="flagged" data-status="{ html.escape( status, quote=True )}">'

    def render( self, plan: PagePlan, rows: list[ dict[ str, Any ]] ) -> Path:
        when    = str( plan.year ) if plan.year is not None else "all years"
        rolled  = ( f" (and { plan.category.children } sub-categories)"
                    if plan.category.children else "" )
        title   = f"{ plan.category.label } -- { when }"
        columns = tuple( c for c in plan.columns if c not in self._HIDDEN )
        flagged = sum( 1 for row in rows if self._status( row ))

        head = "".join( f"<th{ self._class( c )}>{ html.escape( self._LABELS.get( c, c ))}</th>"
                        for c in columns )
        body = "\n".join(
            self._row_open( row ) + "".join( f"<td{ self._class( c )}>{ self._value( c, row.get( c ))}</td>"
                                             for c in columns ) + "</tr>"
            for row in rows )
        key = ( f'<p class="sub"><span class="flag-key"></span> { flagged } '
                f'row{ "" if flagged == 1 else "s" } { html.escape( self._KEY_WORDS )}</p>'
                if flagged else "" )

        if rows:
            at     = columns.index( "amount" ) if "amount" in columns else len( columns ) - 1
            cells  = [ "" ] * len( columns )
            cells[ 0 ]  = "TOTAL"
            cells[ at ] = html.escape( _money( _total( rows )))
            foot   = "<tr>" + "".join( f"<td{ self._class( columns[ i ])}>{ cells[ i ]}</td>"
                                       for i in range( len( columns ))) + "</tr>"
            table  = ( f'<div class="wrap"><table><thead><tr>{ head }</tr></thead>'
                       f"<tbody>\n{ body }\n</tbody><tfoot>{ foot }</tfoot></table></div>" )
        else:
            # A header with no rows is an answer ("nothing is filed here"); a
            # page with no table at all would look like the query never ran.
            table = ( f'<div class="wrap"><table><thead><tr>{ head }</tr></thead></table>'
                      f'<div class="empty">{ html.escape( self._EMPTY_TEXT.format( when=when ))}</div></div>' )

        generated = datetime.now().strftime( "%Y-%m-%d %H:%M" )
        ids       = ( "Category ids: " + html.escape( ", ".join( str( i ) for i in plan.category.ids )) + "."
                      if plan.category.ids else "" )
        page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ html.escape( title )}</title>
<style>{ self._STYLE }</style>
</head>
<body>
<main>
<h1>{ html.escape( plan.category.label )}{ html.escape( rolled )}</h1>
<p class="sub">{ html.escape( plan.org )} &middot; expenses in { html.escape( when )}</p>
<div class="stats">
  <div class="stat"><b>{ len( rows )}</b><span>expenses</span></div>
  <div class="stat"><b>{ html.escape( _money( _total( rows )))}</b><span>total</span></div>
</div>
{ key }
{ table }
<div id="tip" class="tip" role="tooltip" hidden></div>
<footer>{ html.escape( self._MADE_BY.format( generated=generated, adw_id=plan.adw_id ))}
{ ids }</footer>
</main>
<script>{ self._SCRIPT }</script>
</body>
</html>
"""
        plan.path.parent.mkdir( parents=True, exist_ok=True )
        plan.path.write_text( page, encoding="utf-8" )
        return plan.path


class HtmlLinkIndex( LifecycleLogged ):
    """`index.html` beside the pages, newest link first, rebuilt from `index.json`.

    The manifest is the record and the HTML is only ever regenerated from it,
    so the list never depends on parsing a page. Both are written to a temp
    file and renamed into place: a dashboard reloading mid-write sees the old
    list or the new one, never half of one.
    """

    _STYLE = """
:root { --bg: #ffffff; --fg: #1d2330; --muted: #5d6676; --line: #dfe3ea; --accent: #2f5fa7; }
@media ( prefers-color-scheme: dark ) {
  :root { --bg: #14171d; --fg: #e4e8ef; --muted: #9aa3b2; --line: #2c323d; --accent: #8db4f0; }
}
body { margin: 0; padding: 24px 16px; background: var( --bg ); color: var( --fg );
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.4rem; margin: 0 0 16px; }
ol { list-style: none; margin: 0; padding: 0; border: 1px solid var( --line ); border-radius: 8px; }
li { padding: 10px 14px; border-bottom: 1px solid var( --line ); }
li:last-child { border-bottom: 0; }
a { color: var( --accent ); font-weight: 600; word-break: break-all; }
.meta { display: block; color: var( --muted ); font-size: .85rem; }
.empty { color: var( --muted ); }
"""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def _load( self, manifest: Path ) -> list[ PageLink ]:
        """The links already listed. A missing or unreadable manifest is an
        empty list, not a failure -- the page itself was still written."""
        try:
            entries = json.loads( manifest.read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            return []
        links: list[ PageLink ] = []
        for e in entries if isinstance( entries, list ) else []:
            if isinstance( e, dict ) and isinstance( e.get( "file" ), str ):
                links.append( PageLink( file=e[ "file" ], summary=str( e.get( "summary", "" )),
                                        generated=str( e.get( "generated", "" ))))
        return links

    def _write( self, path: Path, text: str ) -> None:
        tmp = path.with_name( path.name + ".tmp" )
        tmp.write_text( text, encoding="utf-8" )
        tmp.replace( path )

    def _html( self, links: list[ PageLink ]) -> str:
        if links:
            items = "\n".join(
                f'<li><a href="{ html.escape( link.file, quote=True )}" target="_blank" rel="noopener">'
                f"{ html.escape( link.file )}</a>"
                f'<span class="meta">{ html.escape( link.summary )} &middot; { html.escape( link.generated )}</span></li>'
                for link in links )
            listing = f"<ol>\n{ items }\n</ol>"
        else:
            listing = '<p class="empty">No ADW-generated reports yet.</p>'
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ADW Generated</title>
<style>{ self._STYLE }</style>
</head>
<body>
<main>
<h1>ADW Generated</h1>
{ listing }
</main>
</body>
</html>
"""

    def publish( self, page: Path, summary: str ) -> PublishedLink:
        folder   = page.parent
        manifest = folder / INDEX_MANIFEST
        link     = PageLink( file=page.name, summary=summary,
                             generated=datetime.now().strftime( "%Y-%m-%d %H:%M:%S" ))
        # A re-published name moves to the top instead of appearing twice.
        links = [ link ] + [ old for old in self._load( manifest ) if old.file != page.name ]
        self._write( manifest, json.dumps([ vars( l ) for l in links ], indent=2 ) + "\n" )
        index = folder / INDEX_PAGE
        self._write( index, self._html( links ))
        return PublishedLink( index=index )


class DashboardHostLinkIndex( LifecycleLogged ):
    """Decorator: publish locally, then onto the dashboard host's list too.

    The remote list is rebuilt the same way the local one is -- its
    `index.json` is fetched into a scratch folder and handed to `remote_index`
    (an ordinary `HtmlLinkIndex`), so the format has one owner. The page and
    the regenerated list are uploaded to a scratch folder on the host and
    moved into place page first, so the dashboard never lists a link whose
    page isn't there yet.

    Only pages written into `mirrored_dir` are copied; a `--out` elsewhere is
    somebody's own file. Every failure becomes the note, never an exception.
    """

    _SSH_OPTS = [ "-o", "BatchMode=yes", "-o", "ConnectTimeout=8" ]

    def __init__( self, inner: IPageIndex, remote_index: IPageIndex, host: str,
                  remote_dir: str, mirrored_dir: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._inner        = inner
        self._remote_index = remote_index
        self._host         = host
        self._remote_dir   = remote_dir
        self._mirrored_dir = mirrored_dir.resolve()

    def _run( self, argv: list[ str ], stdin: str = "" ) -> str:
        """stdout of one ssh/scp call; raises RuntimeError with stderr on failure."""
        try:
            done = subprocess.run( argv, input=stdin, capture_output=True, text=True, timeout=60 )
        except ( OSError, subprocess.TimeoutExpired ) as error:
            raise RuntimeError( str( error ))
        if done.returncode != 0:
            raise RuntimeError( done.stderr.strip() or f"exit status { done.returncode }" )
        return done.stdout

    def _mirror( self, page: Path, summary: str ) -> str:
        folder   = shlex.quote( self._remote_dir )
        incoming = f"{ self._remote_dir }/.incoming-{ page.stem }"
        # One round trip: who the host is, and the list it already shows.
        reply = self._run([ "ssh", *self._SSH_OPTS, self._host,
                            f"hostname; mkdir -p { folder } && cat { folder }/{ INDEX_MANIFEST } 2>/dev/null; true" ])
        remote_name, _, manifest = reply.partition( "\n" )
        if remote_name.strip().lower() == socket.gethostname().lower():
            return ""   # this machine is the dashboard host; the local list is the one shown

        scratch = tempfile.mkdtemp( prefix="adw_dashboard_" )
        try:
            staging = Path( scratch )
            if manifest.strip():
                ( staging / INDEX_MANIFEST ).write_text( manifest, encoding="utf-8" )
            listed = self._remote_index.publish( staging / page.name, summary )
            self._run([ "ssh", *self._SSH_OPTS, self._host, f"mkdir -p { shlex.quote( incoming )}" ])
            self._run([ "scp", "-q", *self._SSH_OPTS, str( page ), str( staging / INDEX_MANIFEST ),
                        str( listed.index ), f"{ self._host }:{ incoming }/" ])
        finally:
            shutil.rmtree( scratch, ignore_errors=True )
        moves = " && ".join(
            f"mv -f { shlex.quote( incoming + '/' + name )} { folder }/"
            for name in ( page.name, INDEX_MANIFEST, INDEX_PAGE ))
        self._run([ "ssh", *self._SSH_OPTS, self._host,
                    f"{ moves } && rmdir { shlex.quote( incoming )}" ])
        return f"Also listed on the dashboard at { self._host } (ROL Finance -> Reports -> ADW Generated)."

    def publish( self, page: Path, summary: str ) -> PublishedLink:
        local = self._inner.publish( page, summary )
        if page.resolve().parent != self._mirrored_dir:
            return local
        try:
            note = self._mirror( page, summary )
        except RuntimeError as error:
            note = ( f"NOT listed on the dashboard: copying to { self._host } failed "
                     f"({ error }). The page is only on this machine." )
        return PublishedLink( index=local.index, note=note )


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records which category's page is being asked for, nothing more."""

    def __init__( self, engineer: str, request: PageRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which category's web page is being asked for, and the org and year it is scoped to" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt,
                    category=r.category or ( f"id { r.category_id }" if r.category_id else "(none)" ),
                    org_type=r.org_type, year=r.year if r.year is not None else "(all)",
                    include_children=r.include_children )


class RenderPagePhase( LifecycleLogged, IPhase ):
    """Hands the rows to the page renderer and records what landed on disk."""

    def __init__( self, renderer: IPageRenderer, plan: PagePlan,
                  rows: list[ dict[ str, Any ]], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._renderer = renderer
        self._plan     = plan
        self._rows     = rows

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="render", kind="code", owner="books",
                            description="Write the queried rows and their total into one self-contained HTML table a browser can open with nothing else" )

    def act( self, handle: IPhaseHandle ) -> Path:
        written = self._renderer.render( self._plan, self._rows )
        handle.log( path=str( written ), rows=len( self._rows ),
                    bytes=written.stat().st_size if written.exists() else 0 )
        return written


class PublishLinkPhase( LifecycleLogged, IPhase ):
    """Puts the page that was just written at the top of the ADW Generated list."""

    def __init__( self, index: IPageIndex, plan: PagePlan, rows: list[ dict[ str, Any ]],
                  written: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._index   = index
        self._plan    = plan
        self._rows    = rows
        self._written = written

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="publish", kind="code", owner="books",
                            description="Add a link to the new page at the top of the ADW Generated list, moving older links down" )

    def act( self, handle: IPhaseHandle ) -> PublishedLink:
        p         = self._plan
        when      = str( p.year ) if p.year is not None else "all years"
        summary   = ( f"{ p.category.label } -- { when } -- { len( self._rows )} expenses, "
                      f"{ _money( _total( self._rows ))}" )
        published = self._index.publish( self._written, summary )
        handle.log( index=str( published.index ), link=self._written.name,
                    dashboard=published.note or "(local list is the dashboard's)" )
        return published


class ReportPhase( LifecycleLogged, IPhase ):
    """The one line, every part of which is readable back off the page."""

    def __init__( self, plan: PagePlan, rows: list[ dict[ str, Any ]],
                  written: Path, published: PublishedLink, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._plan      = plan
        self._rows      = rows
        self._written   = written
        self._published = published

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="books",
                            description="State the category, the row count, the total and the path of the page that was written" )

    def act( self, handle: IPhaseHandle ) -> str:
        p      = self._plan
        when   = f" in { p.year }" if p.year is not None else ""
        rolled = f" (and { p.category.children } sub-categories)" if p.category.children else ""
        if not self._rows:
            message = ( f"{ p.org }: { p.category.label }{ rolled } has no expenses{ when }, "
                        f"so the page at { self._written } has a header and no rows." )
        else:
            message = ( f"{ p.org }: wrote a page of { len( self._rows )} "
                        f"{ p.category.label }{ rolled } expenses{ when } totalling "
                        f"{ _money( _total( self._rows ))} to { self._written }." )
        if self._published.note:
            message = f"{ message } { self._published.note }"
        handle.log( message=message )
        return message


# -- Argument parsing --------------------------------------------------------

class ArgparseCategoryWebPageArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> PageRequest:
        parser = argparse.ArgumentParser(
            description=self._description, formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="build a category web page",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--category", default="",
                             help="the category name, exact or a unique fragment" )
        parser.add_argument( "--category-id", type=int, default=None,
                             help="the category's id, when the name is ambiguous" )
        parser.add_argument( "--include-children", action=argparse.BooleanOptionalAction, default=True,
                             help="roll sub-categories of the matched category into the same page "
                                  "(default on; --no-include-children counts the category's own rows only)" )
        parser.add_argument( "--org-type", default="NONPROFIT",
                             help="whose books: the organizations.type to scope to" )
        parser.add_argument( "--year", type=int, default=None,
                             help="restrict to one calendar year" )
        parser.add_argument( "--out", dest="out_path", default="",
                             help=f"where to write it; default { DEFAULT_OUT_DIR }/<category>_<Month>_<day>_hh_mm_ss.html" )
        parser.add_argument( "--env-file", default=DEFAULT_ENV_FILE,
                             help="file defining NON_PROFIT_USER/PASSWORD/DB_NAME when not exported" )
        parser.add_argument( "--dashboard-host", default=DEFAULT_DASHBOARD_HOST,
                             help="ssh target whose dashboard shows ADW Generated; 'none' keeps the page local" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return PageRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                            category=parsed.category, category_id=parsed.category_id,
                            include_children=parsed.include_children,
                            org_type=parsed.org_type, year=parsed.year,
                            out_path=parsed.out_path, env_file=parsed.env_file,
                            message_out=parsed.message_out, config=parsed.config,
                            adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class CategoryWebPageApplication( LifecycleLogged, IApplication ):
    """Orchestrates the six-phase run. Depends only on interfaces, plus the
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
                  rows_source:     IExpenseRows,
                  renderer:        IPageRenderer,
                  page_index:      IPageIndex,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._books           = books
        self._resolver        = resolver
        self._rows_source     = rows_source
        self._renderer        = renderer
        self._page_index      = page_index

    def _say( self, request: PageRequest, message: str ) -> None:
        """The caller may be a receptionist runner reading --message-out, so
        the operator's line is a contract -- same rule as the sibling ADWs."""
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )

        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))   # Records which page is
        org = self._phase_executor.execute(                                                             # being asked for,
            run, ConnectPhase( self._books, request.org_type, session_log ))                            # nothing more.

        # Missing, unknown or ambiguous is a real answer, not a crash -- and
        # nothing is written, because a plausible page of the wrong category
        # is worse than no page.
        try:
            category = self._phase_executor.execute( run,
                                                     ResolveCategoryPhase( self._resolver, self._books, request, session_log ))
        except RuntimeError as refusal:
            message = f"No page was written. { refusal }"
            self._say( request, message )
            print( message )
            return run.finish( accepted=False, reason=message )
        
        # ************************* MAIN *******************************
        
        years   = ( request.year, ) if request.year is not None else None
        rows    = self._phase_executor.execute( run, QueryPhase( self._rows_source, self._books, org, category, years, session_log )) # get rows
        plan    = self._plan( request, org, category, run.adw_id ) # where the page goes and what it shows
        written = self._phase_executor.execute( run, RenderPagePhase( self._renderer, plan, rows, session_log ))# An empty category is a true
        published = self._phase_executor.execute( run, PublishLinkPhase( self._page_index, plan, rows, written, session_log ))
        message = self._phase_executor.execute( run, ReportPhase( plan, rows, written, published, session_log )) # answer and is accepted as one:
        self._say( request, message )                                                                           # the page exists and says so. 
        return run.finish( accepted=written.exists(), reason=message )                                          # What is NOT accepted is 
                                                                                                                # the file not being there.
        # ************************* END MAIN ***************************                                        
        

    def _plan( self, request: PageRequest, org: OrgScope, category: CategoryScope,
               adw_id: str ) -> PagePlan:
        """Where the page goes and what it shows.

        A `--out` without `.html` gets it appended rather than swapped, the
        same rule the spreadsheet ADW applies to its extensions. The default
        name is stamped to the second, and a clash within that second gets a
        counter, so no run overwrites a page an older link points at.
        """
        if request.out_path:
            path = Path( request.out_path ).expanduser()
            if path.suffix.lower() not in ( ".html", ".htm" ):
                path = path.with_name( path.name + ".html" )
        else:
            now  = datetime.now()
            stem = f"{ _safe_name( category.label )}_{ now:%B}_{ now.day }_{ now:%H_%M_%S}"
            path = DEFAULT_OUT_DIR / f"{ stem }.html"
            n = 2
            while path.exists():
                path = DEFAULT_OUT_DIR / f"{ stem }_{ n }.html"
                n += 1
        return PagePlan( org=org.name, category=category, year=request.year,
                         columns=self._rows_source.columns, adw_id=adw_id,
                         path=path.resolve())


def _env_file( argv: list[ str ]) -> str:
    """The --env-file the caller named, read before the books are built.

    The credentials are wired here, before `run()` parses argv, so without
    this peek --env-file would be accepted and silently ignored -- which is
    what both sibling finance ADWs currently do.
    """
    peek = argparse.ArgumentParser( add_help=False )
    peek.add_argument( "--env-file", default=DEFAULT_ENV_FILE )
    known, _ = peek.parse_known_args( argv )
    return known.env_file


def _dashboard_host( argv: list[ str ]) -> str:
    """The --dashboard-host the caller named, read before the index is built --
    the same peek, for the same reason, as `_env_file`."""
    peek = argparse.ArgumentParser( add_help=False )
    peek.add_argument( "--dashboard-host", default=DEFAULT_DASHBOARD_HOST )
    known, _ = peek.parse_known_args( argv )
    return known.dashboard_host


def build_application( argv: list[ str ]) -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may.

    Every query reaches MySQL only through `ReadOnlyBooks`, the same
    Decorator stack both sibling finance ADWs build.
    """
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    credentials = EnvFileCredentials( _env_file( argv ), bootstrap_log )
    page_index: IPageIndex = HtmlLinkIndex( bootstrap_log )
    host = _dashboard_host( argv )
    if host and host.lower() != "none":
        page_index = DashboardHostLinkIndex( page_index, HtmlLinkIndex( bootstrap_log ), host,
                                             DASHBOARD_DIR, DEFAULT_OUT_DIR, bootstrap_log )
    return CategoryWebPageApplication(
        argument_parser = ArgparseCategoryWebPageArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(             bootstrap_log ),
        session_factory = SessionRunFactory(            bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor(   bootstrap_log ),
        books           = ReadOnlyBooks( PyMySQLLiveBooks( credentials, bootstrap_log ),
                                         bootstrap_log ),
        resolver        = SqlCategoryResolver(          bootstrap_log,
                              NamedCategoryGroups(      bootstrap_log )),
        rows_source     = CategoryExpenseRows(          bootstrap_log ),
        renderer        = HtmlTablePageRenderer(        bootstrap_log ),
        page_index      = page_index,
        logger          =                               bootstrap_log,            )


if __name__ == "__main__":
    sys.exit( build_application( sys.argv[ 1: ]).run( sys.argv[ 1: ]))

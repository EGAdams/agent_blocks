#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
"""ADW Interface Live Finance -- answer one question about the live non-profit
books with real numbers, read-only, with no model anywhere in the path.

Written in the interface style, named after `adw_interface_prompt.py`: every
collaborator is a role from `sssf_roles`, the phases are objects rather than
`with` blocks, and `build_application()` is the only function allowed to name
a concrete class. `ManualContextPhaseExecutor` drives `run.phase()`'s protocol
by hand, so there is no `with` and no `yield` here.

WHY THIS EXISTS

Toyota had no way to read the real books. Her one data-shaped assignment was
`rol_receptionist_training` -- synthetic expense #900001, $12.34 -- and her
capability text told her so on every turn. Asked for an actual figure she did
the only thing left to her: she produced a plausible one from recalled
conversation. Sixty-six stored turns of training talk then came back as
history and hardened the habit.

A prompt rule cannot fix that, because the missing thing is not instruction,
it is a number. So this ADW fetches the number. Nothing in this file can
phrase, round, summarize or soften it: the phases run SQL and format rows,
and the receptionist-side runner reports the line verbatim. The same
deterministic-first order Mickey's tracker uses -- collect the facts before
any model sees them -- for the same reason: the explanation must not be able
to add a fact the query did not return.

READ-ONLY BY CONSTRUCTION, NOT BY PERMISSION

The MySQL account on this box holds ALL PRIVILEGES on `nonprofit_finance`, so
grants cannot be the safeguard. Two things in this file are:

* `ReadOnlyBooks` is a Decorator over `ILiveBooks` that refuses any statement
  that is not a single SELECT (or WITH ... SELECT) -- no semicolons, no DML
  keyword, no multiple statements. Every question's SQL goes through it.
* `PyMySQLLiveBooks` opens the session with `SET SESSION TRANSACTION ISOLATION
  LEVEL REPEATABLE READ, READ ONLY` and never commits, so even a statement
  that slipped past the Decorator would be refused by the server.

These are belt and suspenders on purpose. This workflow reads the real books
of a real organization; a bug here is somebody's accounting record.

QUESTIONS ARE A STRATEGY REGISTRY, NOT AN IF/ELIF

Each question is an `ILiveQuestion`: the words that select it, the SQL it
runs, and how it reads its own rows back as one English line.
`QuestionCatalog` picks the first that matches. Adding a question is one entry
in `install_questions()` and nothing else -- no phase changes, no new flag, no
edit to the receptionist. Removing one is deleting that entry. That is the
whole reason the questions are objects: the user wants an ADW in the path even
where a function would do, so that swapping the thing on either end stays a
one-line change.

WHEN NOTHING MATCHES, NOTHING IS ANSWERED

`select_question` returning nothing is a normal outcome, not an error. The
operator line then names the questions that ARE installed and says plainly
that no figure was read. It never falls through to a default query, because a
number answering a question nobody asked is exactly the failure this workflow
was built to end. Same rule as `UnwiredRunner` on the receptionist side, and
as `adw_interface_search_open.py` opening the search page rather than inventing
a destination.

Six phases:

* `request` (engineer) -- record the question as asked, and the org scope.
* `select_question` (code) -- the Strategy registry picks one question, or
  none. Touches no database.
* `connect` (code) -- open the read-only session and resolve which
  organization's books are in scope, by `org_type` rather than a pinned id,
  so renumbering the table cannot silently retarget the query.
* `query` (code) -- run the selected question's single SELECT through the
  read-only Decorator.
* `report` (code) -- the question reads its own rows back as one line, and
  the rows go to `--json-out` for a caller that wants the table.

No agent phase: reading a figure needs no judgement, so `REQUIRED_AGENTS` is
empty and every phase is `kind="code"`.

CREDENTIALS

From the environment, or from an env file named by `--env-file` (default
`/home/adamsl/.config/mazda/finance-db.env`): NON_PROFIT_USER,
NON_PROFIT_PASSWORD, NON_PROFIT_DB_NAME (default `nonprofit_finance`),
DB_HOST (default `127.0.0.1`), DB_PORT (default 3306). The same variables
`finance_team/roles.py` briefs its roles with. Nothing is read from a literal
in this file and no password is ever logged or written to `--message-out`.

Usage:
    uv run adws/adw_interface_live_finance.py "<why this is being asked>" \\
        --question "how much have we spent this year" \\
        [--org-type NONPROFIT] [--year 2026] [--limit 10] \\
        [--list-questions] [--message-out /tmp/line.txt] \\
        [--json-out /tmp/rows.json] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(select_question) -> code(connect)
        -> code(query) -> code(report)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, Sequence

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams
from adw_modules.runner import Run

REQUIRED_AGENTS: list[ str ] = []   # every phase here is code; no judgement is needed

DEFAULT_ENV_FILE = "/home/adamsl/.config/mazda/finance-db.env"


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class LiveFinanceRequest:
    """Parsed command-line intent. Mirrors sssf_roles.PromptRequest's shape."""
    prompt:         str
    question:       str
    org_type:       str
    year:           int | None
    limit:          int
    list_questions: bool
    env_file:       str
    message_out:    str
    json_out:       str
    config:         str
    adw_id:         str | None


@dataclass( frozen=True )
class OrgScope:
    """Which organization's books the query is restricted to."""
    org_id:   int
    name:     str
    org_type: str


@dataclass( frozen=True )
class QuestionAsked:
    """What the question needs to know beyond its own words."""
    text:  str
    year:  int | None
    limit: int
    scope: OrgScope


# -- Roles the questions and the database are programmed against -------------

class ILiveBooks( Protocol ):
    """A read path into the live books. Deliberately has no write method.

    One method, because one is all a question needs: the narrower this role
    is, the less there is for a future question to misuse. An implementation
    that could write would still be refused by `ReadOnlyBooks`, but the shape
    of the role is the first line of that argument.
    """

    def fetch( self, sql: str, params: Sequence[ Any ] = () ) -> list[ dict[ str, Any ]]: ...


class ILiveQuestion( Protocol ):
    """One answerable question about the books: its words, its SQL, its line.

    Strategy. The three methods are deliberately kept together rather than
    split across a matcher, a query builder and a formatter: a question that
    reads its own rows back cannot drift from the columns it selected, which
    is the bug that splitting them would invite.
    """

    @property
    def name( self ) -> str: ...

    @property
    def summary( self ) -> str:
        """One short phrase, for the line that lists what IS installed."""
        ...

    def matches( self, text: str ) -> bool: ...

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]: ...

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str: ...


class IQuestionCatalog( Protocol ):
    """The installed questions, and which one a request selects."""

    def select( self, text: str ) -> ILiveQuestion | None: ...

    def names( self ) -> tuple[ str, ... ]: ...

    def summaries( self ) -> tuple[ str, ... ]: ...


# -- Adapters ----------------------------------------------------------------

class EnvFileCredentials( LifecycleLogged ):
    """Reads DB settings from the environment, topped up from an env file.

    The environment wins: an already-exported value is the operator's explicit
    choice, and silently preferring the file would make a shell override look
    like it had worked when it had not. Same posture as the receptionist's
    `os.environ.setdefault` loading of its own .env.
    """

    def __init__( self, env_file: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._env_file = env_file

    def settings( self ) -> dict[ str, Any ]:
        values = dict( os.environ )
        path = Path( self._env_file )
        if path.is_file():
            for line in path.read_text().splitlines():
                stripped = line.strip().removeprefix( "export " )
                if not stripped or stripped.startswith( "#" ) or "=" not in stripped:
                    continue
                key, _, value = stripped.partition( "=" )
                values.setdefault( key.strip(), value.strip().strip( "\"'" ))
        missing = [ k for k in ( "NON_PROFIT_USER", "NON_PROFIT_PASSWORD" ) if not values.get( k )]
        if missing:
            raise RuntimeError(
                f"Missing { ', '.join( missing )}. Export them, or point --env-file at a "
                f"file that defines them (tried { self._env_file })."
            )
        return {
            "host":     values.get( "DB_HOST", "127.0.0.1" ),
            "port":     int( values.get( "DB_PORT", "3306" )),
            "user":     values[ "NON_PROFIT_USER" ],
            "password": values[ "NON_PROFIT_PASSWORD" ],
            "database": values.get( "NON_PROFIT_DB_NAME", "nonprofit_finance" ),
        }


class PyMySQLLiveBooks( LifecycleLogged ):
    """The only class here that knows the books live in MySQL.

    Opens its session READ ONLY and never commits, so the server itself
    refuses a write even if one reached it. The connection is made on first
    `fetch` so that `--list-questions` and an unmatched question cost no
    database round trip at all.
    """

    def __init__( self, credentials: EnvFileCredentials, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._credentials = credentials
        self._connection  = None

    def _connect( self ):
        if self._connection is None:
            import pymysql
            from pymysql.cursors import DictCursor

            settings = self._credentials.settings()
            self._connection = pymysql.connect( **settings, cursorclass=DictCursor,
                                                autocommit=False, connect_timeout=10 )
            cursor = self._connection.cursor()
            try:
                # Belt to the Decorator's suspenders: past this point the
                # server will reject a write regardless of what was sent.
                cursor.execute( "SET SESSION TRANSACTION ISOLATION LEVEL "
                                "REPEATABLE READ, READ ONLY" )
            finally:
                cursor.close()
        return self._connection

    def fetch( self, sql: str, params: Sequence[ Any ] = () ) -> list[ dict[ str, Any ]]:
        cursor = self._connect().cursor()
        try:
            cursor.execute( sql, tuple( params ))
            return list( cursor.fetchall())
        finally:
            cursor.close()

    def close( self ) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


class ReadOnlyBooks( LifecycleLogged ):
    """Decorator over ILiveBooks that refuses anything but one SELECT.

    Not a convenience. The account this runs as can drop the schema, so the
    guarantee that Toyota only ever reads has to be made in code that every
    question's SQL passes through. A statement is accepted only if it is a
    single SELECT (or WITH ... SELECT), carries no statement separator, and
    contains no write keyword as a whole word.

    Raising rather than returning empty rows is deliberate: an empty result
    reads as "nothing matched", which would turn a rejected write into a
    believable zero.
    """

    _ALLOWED_START = re.compile( r"^\s*(select|with)\b", re.IGNORECASE )
    _FORBIDDEN     = re.compile(
        r"\b(insert|update|delete|replace|truncate|drop|alter|create|grant|revoke"
        r"|commit|rollback|lock|unlock|rename|load|call|do|handler|set)\b",
        re.IGNORECASE,
    )

    def __init__( self, inner: ILiveBooks, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._inner = inner

    def fetch( self, sql: str, params: Sequence[ Any ] = () ) -> list[ dict[ str, Any ]]:
        self.assert_read_only( sql )
        return self._inner.fetch( sql, params )

    @classmethod
    def assert_read_only( cls, sql: str ) -> None:
        """Public so a test can assert the refusals without a database."""
        if not cls._ALLOWED_START.match( sql ):
            raise PermissionError( "Only a SELECT may be run against the live books." )
        if ";" in sql.strip().rstrip( ";" ) or sql.strip().count( ";" ) > 1:
            raise PermissionError( "Only one statement may be run against the live books." )
        found = cls._FORBIDDEN.search( sql )
        if found:
            raise PermissionError(
                f"Refusing a statement containing { found.group( 1 ).upper()}: "
                "the live books are read-only here."
            )


class QuestionCatalog( LifecycleLogged ):
    """The installed questions, in the order they are offered a request.

    First match wins, so `install_questions()` puts the specific ones ahead of
    the broad ones. Ordering is data, not logic -- which is the point of it
    being a list in one function rather than a chain of elifs across the file.
    """

    def __init__( self, questions: Sequence[ ILiveQuestion ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        if not questions:
            raise ValueError( "At least one live question must be installed." )
        self._questions = tuple( questions )

    def select( self, text: str ) -> ILiveQuestion | None:
        for question in self._questions:
            if question.matches( text ):
                return question
        return None

    def names( self ) -> tuple[ str, ... ]:
        return tuple( q.name for q in self._questions )

    def summaries( self ) -> tuple[ str, ... ]:
        return tuple( f"{ q.name }: { q.summary }" for q in self._questions )


# -- The questions themselves ------------------------------------------------

def _money( value: Any ) -> str:
    """One consistent money format, so no question invents its own."""
    return f"${ Decimal( str( value or 0 )):,.2f}"


def _year_clause( asked: QuestionAsked, column: str = "expense_date" ) -> tuple[ str, list[ Any ]]:
    """An optional year restriction, as SQL plus its parameters.

    Returns an always-true fragment rather than an empty string when no year
    was given, so every question can interpolate it unconditionally and none
    has to assemble its own WHERE by hand.
    """
    if asked.year is None:
        return "", []
    return f" AND YEAR( e.{ column }) = %s", [ asked.year ]


def _scope_line( asked: QuestionAsked ) -> str:
    """How a spoken line names the books it read."""
    when = f" in { asked.year }" if asked.year is not None else ""
    return f"{ asked.scope.name }{ when }"


class _Question:
    """Shared plumbing for the installed questions. Not a role, an abbreviation.

    Each subclass supplies its trigger words, its SQL and its line. Keeping
    the matcher as a word list rather than a bespoke regex per question is
    what makes a new question a data change.
    """

    name:     str = ""
    summary:  str = ""
    triggers: tuple[ re.Pattern[ str ], ... ] = ()

    def matches( self, text: str ) -> bool:
        return any( pattern.search( text ) for pattern in self.triggers )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        raise NotImplementedError

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        raise NotImplementedError


def _words( *alternatives: str ) -> tuple[ re.Pattern[ str ], ... ]:
    return tuple( re.compile( alternative, re.IGNORECASE ) for alternative in alternatives )


class TotalSpendQuestion( _Question ):
    """How much was spent, over how many expenses, across what date span."""

    name     = "total_spend"
    summary  = "total spent, the number of expenses, and the dates they span"
    triggers = _words(
        r"\b(how much|total|totals|altogether|all in|sum)\b.{0,40}\b(spen|spent|spending|paid|expense|cost)",
        r"\b(spen|spent|spending|paid out)\b.{0,30}\b(total|altogether|so far|this year|in total)",
        r"\btotal (spend|spending|expenses|amount)\b",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT COUNT(*) AS expenses, COALESCE( SUM( e.amount ), 0 ) AS total, "
            "       MIN( e.expense_date ) AS first_date, MAX( e.expense_date ) AS last_date "
            "FROM expenses e "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql,
            [ asked.scope.org_id, *year_params ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        row = rows[ 0 ] if rows else {}
        count = int( row.get( "expenses" ) or 0 )
        if count == 0:
            return f"{ _scope_line( asked )} has no expenses recorded for that period."
        return ( f"{ _scope_line( asked )}: { _money( row.get( 'total' ))} across "
                 f"{ count:,} expenses, { row.get( 'first_date' )} through "
                 f"{ row.get( 'last_date' )}." )


class SpendByMonthQuestion( _Question ):
    """The month-by-month shape of spending."""

    name     = "spend_by_month"
    summary  = "spending totalled by month"
    triggers = _words(
        r"\b(by|per|each|every)\s+month\b",
        r"\bmonth(ly)?\b.{0,30}\b(spend|spending|total|breakdown|summary|report)",
        r"\b(spend|spending|expenses)\b.{0,20}\bmonth",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT DATE_FORMAT( e.expense_date, '%%Y-%%m' ) AS month, "
            "       COUNT(*) AS expenses, COALESCE( SUM( e.amount ), 0 ) AS total "
            "FROM expenses e "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql +
            " GROUP BY month ORDER BY month DESC LIMIT %s",
            [ asked.scope.org_id, *year_params, asked.limit ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        if not rows:
            return f"{ _scope_line( asked )} has no monthly totals for that period."
        parts = [ f"{ row[ 'month' ]} { _money( row[ 'total' ])}" for row in rows ]
        return ( f"{ _scope_line( asked )}, most recent { len( rows )} months: "
                 + "; ".join( parts ) + "." )


class SpendByCategoryQuestion( _Question ):
    """Where the money went, by the books' own category names."""

    name     = "spend_by_category"
    summary  = "the largest categories by amount spent"
    triggers = _words(
        r"\b(by|per|each|which|what)\s+categor",
        r"\bcategor(y|ies)\b.{0,30}\b(spend|spending|total|breakdown|biggest|largest|most)",
        r"\b(where|what)\b.{0,30}\b(money|spending) (go|going|went)\b",
        r"\bbiggest (categor|expense categor)",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT COALESCE( c.name, '(uncategorized)' ) AS category, "
            "       COUNT(*) AS expenses, COALESCE( SUM( e.amount ), 0 ) AS total "
            "FROM expenses e LEFT JOIN categories c ON c.id = e.category_id "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql +
            " GROUP BY category ORDER BY total DESC LIMIT %s",
            [ asked.scope.org_id, *year_params, asked.limit ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        if not rows:
            return f"{ _scope_line( asked )} has no categorized spending for that period."
        parts = [ f"{ row[ 'category' ]} { _money( row[ 'total' ])}" for row in rows ]
        return ( f"{ _scope_line( asked )}, top { len( rows )} categories by amount: "
                 + "; ".join( parts ) + "." )


class TopVendorsQuestion( _Question ):
    """Who was paid the most."""

    name     = "top_vendors"
    summary  = "the vendors or merchants paid the most"
    triggers = _words(
        r"\b(vendor|merchant|supplier|payee)s?\b",
        r"\bwho\b.{0,30}\b(paid|pay|getting paid|most)\b",
        r"\b(top|biggest|largest)\b.{0,20}\b(vendor|merchant|supplier|payee)",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT COALESCE( m.name, '(no merchant on file)' ) AS vendor, "
            "       COUNT(*) AS expenses, COALESCE( SUM( e.amount ), 0 ) AS total "
            "FROM expenses e LEFT JOIN merchants m ON m.id = e.merchant_id "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql +
            " GROUP BY vendor ORDER BY total DESC LIMIT %s",
            [ asked.scope.org_id, *year_params, asked.limit ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        if not rows:
            return f"{ _scope_line( asked )} has no vendor totals for that period."
        parts = [ f"{ row[ 'vendor' ]} { _money( row[ 'total' ])}" for row in rows ]
        return ( f"{ _scope_line( asked )}, top { len( rows )} by amount paid: "
                 + "; ".join( parts ) + "." )


class MissingReceiptsQuestion( _Question ):
    """Which recorded expenses have no receipt on file.

    The honest wording matters here: this reports rows with no receipt URL
    recorded, which is not the same as a receipt not existing. Saying the
    stronger thing is the kind of claim the whole workflow exists to avoid.
    """

    name     = "missing_receipts"
    summary  = "expenses with no receipt recorded"
    triggers = _words(
        r"\b(missing|no|without|lacking)\b.{0,20}\breceipt",
        r"\breceipts?\b.{0,20}\b(missing|needed|outstanding|gap)",
        r"\bwhich\b.{0,30}\bneed\b.{0,15}\breceipt",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT COUNT(*) AS expenses, COALESCE( SUM( e.amount ), 0 ) AS total "
            "FROM expenses e "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM' "
            "  AND ( e.receipt_url IS NULL OR e.receipt_url = '' )"
            + year_sql,
            [ asked.scope.org_id, *year_params ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        row = rows[ 0 ] if rows else {}
        count = int( row.get( "expenses" ) or 0 )
        if count == 0:
            return f"{ _scope_line( asked )}: every expense has a receipt recorded."
        return ( f"{ _scope_line( asked )}: { count:,} expenses totalling "
                 f"{ _money( row.get( 'total' ))} have no receipt recorded. That is the "
                 "receipt field being empty, not proof the receipt doesn't exist." )


class UnverifiedQuestion( _Question ):
    """How much of the books a person has not yet confirmed."""

    name     = "unverified"
    summary  = "expenses not yet marked human-verified"
    triggers = _words(
        r"\b(unverified|not verified|un-reviewed|unreviewed|needs? review|to review)\b",
        r"\bverif(ied|y|ication)\b",
        r"\bhow much\b.{0,30}\b(checked|reviewed|confirmed)\b",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT SUM( e.human_verified = 1 ) AS verified, "
            "       SUM( e.human_verified = 0 OR e.human_verified IS NULL ) AS unverified, "
            "       COUNT(*) AS expenses "
            "FROM expenses e "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql,
            [ asked.scope.org_id, *year_params ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        row = rows[ 0 ] if rows else {}
        total = int( row.get( "expenses" ) or 0 )
        if total == 0:
            return f"{ _scope_line( asked )} has no expenses recorded for that period."
        unverified = int( row.get( "unverified" ) or 0 )
        verified   = int( row.get( "verified" ) or 0 )
        return ( f"{ _scope_line( asked )}: { verified:,} of { total:,} expenses are marked "
                 f"human-verified, { unverified:,} are not." )


class RecentExpensesQuestion( _Question ):
    """The latest entries, as a bookkeeper would read them off."""

    name     = "recent_expenses"
    summary  = "the most recently dated expenses"
    triggers = _words(
        r"\b(recent|latest|last|newest)\b.{0,25}\b(expense|entr|transaction|purchase|charge)",
        r"\bwhat('s| is| has|'ve| have)\b.{0,25}\b(come in|been entered|been added)\b",
        r"\blast few\b",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT e.expense_date, e.amount, "
            "       COALESCE( NULLIF( e.description, '' ), '(no description)' ) AS description, "
            "       COALESCE( c.name, '(uncategorized)' ) AS category "
            "FROM expenses e LEFT JOIN categories c ON c.id = e.category_id "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql +
            " ORDER BY e.expense_date DESC, e.id DESC LIMIT %s",
            [ asked.scope.org_id, *year_params, asked.limit ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        if not rows:
            return f"{ _scope_line( asked )} has no expenses recorded for that period."
        parts = [ f"{ row[ 'expense_date' ]} { _money( row[ 'amount' ])} { row[ 'description' ]}"
                  for row in rows ]
        return ( f"{ _scope_line( asked )}, { len( rows )} most recent: "
                 + "; ".join( parts ) + "." )


class PaymentMethodQuestion( _Question ):
    """The split across cash, card, bank and other -- the partition the
    finance team's four mapper roles are built on."""

    name     = "spend_by_method"
    summary  = "spending split across cash, card, bank and other"
    triggers = _words(
        r"\b(by|per|each)\s+(payment )?method\b",
        r"\b(cash|card|bank|check)\b.{0,30}\b(split|breakdown|versus|vs|compared)",
        r"\bhow (was|were|did we)\b.{0,20}\bpa(y|id)\b",
    )

    def sql( self, asked: QuestionAsked ) -> tuple[ str, Sequence[ Any ]]:
        year_sql, year_params = _year_clause( asked )
        return (
            "SELECT e.method, COUNT(*) AS expenses, "
            "       COALESCE( SUM( e.amount ), 0 ) AS total "
            "FROM expenses e "
            "WHERE e.org_id = %s AND e.expense_role <> 'LINE_ITEM'"
            + year_sql +
            " GROUP BY e.method ORDER BY total DESC",
            [ asked.scope.org_id, *year_params ],
        )

    def speak( self, rows: list[ dict[ str, Any ]], asked: QuestionAsked ) -> str:
        if not rows:
            return f"{ _scope_line( asked )} has no expenses recorded for that period."
        parts = [ f"{ row[ 'method' ]} { _money( row[ 'total' ])} over { int( row[ 'expenses' ]):,}"
                  for row in rows ]
        return f"{ _scope_line( asked )}, by payment method: " + "; ".join( parts ) + "."


def install_questions() -> tuple[ ILiveQuestion, ... ]:
    """The installed questions, most specific first. The pluggable seam.

    First match wins, so order is the only coupling between entries: the
    narrow phrasings go above the broad ones. `total_spend` is last of the
    amount questions because "how much did we spend by month" should reach
    `spend_by_month`, not be swallowed by the general total.
    """
    return (
        MissingReceiptsQuestion(),
        UnverifiedQuestion(),
        SpendByMonthQuestion(),
        SpendByCategoryQuestion(),
        PaymentMethodQuestion(),
        TopVendorsQuestion(),
        RecentExpensesQuestion(),
        TotalSpendQuestion(),
    )


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records what is being asked of the live books, nothing more."""

    def __init__( self, engineer: str, request: LiveFinanceRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which question is being asked of the live books, and the org and year it is scoped to" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, question=r.question or "(none)", org_type=r.org_type,
                    year=r.year if r.year is not None else "(all)", limit=r.limit )


class SelectQuestionPhase( LifecycleLogged, IPhase ):
    """Picks the one installed question the request matches, or none.

    Touches no database: an unmatched question must cost nothing, because the
    honest answer to it is a sentence, not a figure.
    """

    def __init__( self, catalog: IQuestionCatalog, text: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._catalog = catalog
        self._text    = text

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="select_question", kind="code", owner="books",
                            description="Match the request against the installed questions, picking one or none, without touching the database" )

    def act( self, handle: IPhaseHandle ) -> ILiveQuestion | None:
        question = self._catalog.select( self._text )
        handle.log( selected=question.name if question else "(none)",
                    installed=", ".join( self._catalog.names()))
        return question


class ConnectPhase( LifecycleLogged, IPhase ):
    """Opens the read-only session and resolves whose books are in scope.

    The organization is found by `org_type`, not by a pinned id: this schema
    holds a non-profit alongside two personal sets of books, and a query that
    hardcoded `org_id = 1` would keep running happily while pointing at the
    wrong one if that table were ever renumbered.
    """

    def __init__( self, books: ILiveBooks, org_type: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._books    = books
        self._org_type = org_type

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="connect", kind="code", owner="books",
                            description="Open the read-only session and resolve which organization's books the query is restricted to" )

    def act( self, handle: IPhaseHandle ) -> OrgScope:
        # `type` is the column's real name and a reserved word, so it is
        # backquoted and aliased once here rather than spelled both ways
        # across the file.
        rows = self._books.fetch(
            "SELECT id, name, `type` AS org_type FROM organizations "
            "WHERE `type` = %s ORDER BY id LIMIT 2",
            [ self._org_type ],
        )
        if not rows:
            raise RuntimeError( f"No organization of type { self._org_type } is on file." )
        if len( rows ) > 1:
            # Two would make "the non-profit's books" ambiguous, and guessing
            # which is a number attributed to the wrong organization.
            raise RuntimeError(
                f"More than one organization has type { self._org_type }; "
                "name the one you mean before any figure is read."
            )
        scope = OrgScope( org_id=int( rows[ 0 ][ "id" ]), name=str( rows[ 0 ][ "name" ]),
                          org_type=str( rows[ 0 ][ "org_type" ]))
        handle.log( org_id=scope.org_id, org=scope.name, org_type=scope.org_type )
        return scope


class QueryPhase( LifecycleLogged, IPhase ):
    """Runs the selected question's single SELECT through the read-only Decorator."""

    def __init__( self, books: ILiveBooks, question: ILiveQuestion, asked: QuestionAsked,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._books    = books
        self._question = question
        self._asked    = asked

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="query", kind="code", owner="books",
                            description="Run the selected question's single read-only SELECT against the live books and keep its rows" )

    def act( self, handle: IPhaseHandle ) -> list[ dict[ str, Any ]]:
        sql, params = self._question.sql( self._asked )
        rows = self._books.fetch( sql, params )
        handle.log( question=self._question.name, rows=len( rows ))
        return rows


class ReportPhase( LifecycleLogged, IPhase ):
    """The question reads its own rows back as one line. Nothing rephrases it."""

    def __init__( self, question: ILiveQuestion | None, rows: list[ dict[ str, Any ]],
                  asked: QuestionAsked | None, catalog: IQuestionCatalog,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._question = question
        self._rows     = rows
        self._asked    = asked
        self._catalog  = catalog

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="report", kind="code", owner="books",
                            description="Turn the rows into the operator's one line, or say plainly that no installed question matched" )

    def act( self, handle: IPhaseHandle ) -> str:
        if self._question is None or self._asked is None:
            message = ( "I didn't read any figure: that isn't one of the questions I can ask "
                        "the books yet. What I can ask: "
                        + "; ".join( self._catalog.summaries()) + "." )
        else:
            message = self._question.speak( self._rows, self._asked )
        handle.log( message=message )
        return message


# -- Argument parsing --------------------------------------------------------

class ArgparseLiveFinanceArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> LiveFinanceRequest:
        parser = argparse.ArgumentParser( description=self._description )
        parser.add_argument( "prompt", nargs="?", default="read the live books",
                             help="inline text or a path to a prompt file; why this is being asked" )
        parser.add_argument( "--question", default="",
                             help="the question to ask the books, in the user's own words" )
        parser.add_argument( "--org-type", default="NONPROFIT",
                             help="whose books: the organizations.org_type to scope to" )
        parser.add_argument( "--year", type=int, default=None,
                             help="restrict to one calendar year" )
        parser.add_argument( "--limit", type=int, default=10,
                             help="how many rows a grouped question may report" )
        parser.add_argument( "--list-questions", action="store_true",
                             help="print the installed questions and exit, touching no database" )
        parser.add_argument( "--env-file", default=DEFAULT_ENV_FILE,
                             help="file defining NON_PROFIT_USER/PASSWORD/DB_NAME when not exported" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--json-out", default="",
                             help="write the queried rows here as JSON" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        # A bare --limit of 0 or less would silently return nothing and read as
        # "the books are empty", so it is clamped rather than trusted.
        return LiveFinanceRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                                   question=parsed.question, org_type=parsed.org_type,
                                   year=parsed.year, limit=max( 1, min( parsed.limit, 200 )),
                                   list_questions=parsed.list_questions,
                                   env_file=parsed.env_file, message_out=parsed.message_out,
                                   json_out=parsed.json_out, config=parsed.config,
                                   adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class LiveFinanceApplication( LifecycleLogged, IApplication ):
    """Orchestrates the five-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares -- the same exception the
    shipped template itself makes (`IPhaseExecutor.execute(run: Run, ...)`).
    """

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  books:           ILiveBooks,
                  catalog:         IQuestionCatalog,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._books           = books
        self._catalog         = catalog

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )

        # Listing what is installed is a question about this workflow, not
        # about the books, so it answers before a session is ever created.
        if request.list_questions:
            for summary in self._catalog.summaries():
                print( summary )
            return 0

        cfg = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))

        text     = request.question.strip() or request.prompt
        question = self._phase_executor.execute(
            run, SelectQuestionPhase( self._catalog, text, session_log ))

        rows:  list[ dict[ str, Any ]] = []
        asked: QuestionAsked | None    = None
        if question is not None:
            scope = self._phase_executor.execute(
                run, ConnectPhase( self._books, request.org_type, session_log ))
            asked = QuestionAsked( text=text, year=request.year, limit=request.limit, scope=scope )
            rows  = self._phase_executor.execute(
                run, QueryPhase( self._books, question, asked, session_log ))

        message = self._phase_executor.execute(
            run, ReportPhase( question, rows, asked, self._catalog, session_log ))

        # The caller is a receptionist runner rather than a terminal. It reads
        # this file, so the operator's line is a contract -- same rule as
        # adw_interface_browser_open.py and adw_map_open.py.
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )
        if request.json_out:
            Path( request.json_out ).write_text(
                json.dumps( rows, indent=2, default=str ) + "\n" )

        # Not matching a question is a true, useful outcome and is accepted as
        # one: the run did exactly what it should, and the line says so. What
        # would NOT be accepted is a matched question that returned nothing at
        # all, which means the query and the schema have drifted apart.
        accepted = question is None or bool( rows )
        return run.finish( accepted=accepted, reason=message )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may.

    The Decorator stack is the point of interest: every question's SQL reaches
    MySQL only through `ReadOnlyBooks`, so the read-only guarantee is a fact
    about this wiring rather than a rule each question has to remember.
    """
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    credentials = EnvFileCredentials( DEFAULT_ENV_FILE, bootstrap_log )
    return LiveFinanceApplication(
        argument_parser = ArgparseLiveFinanceArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(                  bootstrap_log          ),
        session_factory = SessionRunFactory(                 bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(        bootstrap_log          ),
        books           = ReadOnlyBooks( PyMySQLLiveBooks( credentials, bootstrap_log ),
                                         bootstrap_log ),
        catalog         = QuestionCatalog( install_questions(), bootstrap_log ),
        logger          =                                    bootstrap_log,          )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))

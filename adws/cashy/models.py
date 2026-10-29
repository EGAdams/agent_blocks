"""The values that cross between Cashy's pieces. Data only."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from browser_workflows.models import Period


def utc_now() -> str:
    return datetime.now( timezone.utc ).isoformat( timespec="seconds" )


# -- state ---------------------------------------------------------------------

#: Where a conversation rests between turns (the GoF State objects).
SessionStateName = Literal[ "idle", "awaiting_user_login", "ready", "awaiting_user_input", "paused" ]

#: What Cashy is doing inside a turn. Reported to observers, never rested in.
Activity = Literal[ "connecting", "navigating", "inspecting_dom", "executing_adw", "exploring",
                    "downloading", "learning", "completed", "failed" ]

TaskStatus = Literal[ "none", "running", "waiting", "paused", "completed", "failed" ]


# -- banks ---------------------------------------------------------------------

class GoalVocabulary( BaseModel ):
    """The words a bank's site uses for one destination, best first, and the
    words that say Cashy has arrived there."""
    model_config = ConfigDict( frozen=True )

    words:   list[ str ]
    arrived: list[ str ] = Field( default_factory=list )


class BankProfile( BaseModel ):
    """Everything bank-specific, as data. A new bank is one of these."""
    model_config = ConfigDict( frozen=True )

    key:              str
    title:            str
    names:            list[ str ]
    login_url:        str
    hosts:            list[ str ]
    signed_in_words:  list[ str ] = Field( default_factory=lambda: [ "sign out", "log out", "log off", "logout", "signout" ])
    goals:            dict[ str, GoalVocabulary ] = Field( default_factory=dict )


# -- the page ------------------------------------------------------------------

OptionKind = Literal[ "link", "button", "field", "list", "other" ]


class PageOption( BaseModel ):
    """One thing on the page a person could act on, numbered as Cashy said it."""
    number: int
    kind:   OptionKind
    tag:    str
    text:   str
    css:    str
    href:   str = ""
    label:  str = ""


class PageSnapshot( BaseModel ):
    url:       str
    title:     str
    options:   list[ PageOption ]
    signed_in: bool | None = None          # None: cannot tell from the page
    login_form: bool = False


class RankedOption( BaseModel ):
    option: PageOption
    score:  float
    why:    str = ""


class Risk( BaseModel ):
    consequential: bool
    reason:        str = ""


# -- conversation --------------------------------------------------------------

IntentKind = Literal[
    "open_bank", "logged_in", "go_to", "click", "click_number", "list_options", "go_back", "stop", "pause",
    "resume", "try_another", "remember", "save_adw", "get_statement", "find_transaction", "status",
    "confirm", "deny", "answer", "help", "unknown",
]


class Intent( BaseModel ):
    kind:    IntentKind
    text:    str = ""                      # the sentence it came from
    bank:    str = ""                      # a BankProfile key
    goal:    str = ""                      # a goal key, e.g. "statements"
    target:  str = ""                      # words naming an element ("Statements")
    number:  int = 0                       # "the second one" -> 2
    period:  Period | None = None
    account: str = ""                      # a masked account, "•6285"
    again:   bool = False                  # "download it again"


class Question( BaseModel ):
    """What Cashy is waiting to be told, and what to do with the answer."""
    about:   Literal[ "period", "account", "choice", "approval", "bank", "transaction" ]
    prompt:  str
    choices: list[ PageOption ] = Field( default_factory=list )
    hints:   list[ "TransferHint" ] = Field( default_factory=list )
    resume:  Intent | None = None          # the request the answer completes


class NavigationStep( BaseModel ):
    """One move Cashy made, as it would be replayed. No values, ever."""
    action:     Literal[ "click", "back", "open" ]
    text:       str = ""
    css:        str = ""
    tag:        str = ""
    label:      str = ""
    url_before: str = ""
    url_after:  str = ""
    undone:     bool = False


class CashyTask( BaseModel ):
    goal:    str = ""                      # statements | statement_download | transfer_activity | login | ...
    period:  Period | None = None
    account: str = ""
    subject: str = ""                      # "Housing Gas Bill"
    status:  TaskStatus = "none"
    detail:  str = ""
    mode:    Literal[ "", "discovery", "playback" ] = ""
    tried:   list[ str ] = Field( default_factory=list )   # option css already tried for this goal


class CashySession( BaseModel ):
    """What survives between turns. Sanitised before it is saved: no query
    strings, account numbers masked, no field values."""
    id:              str = "default"
    state:           SessionStateName = "idle"
    bank:            str = ""
    target_id:       str = ""
    url:             str = ""
    task:            CashyTask = Field( default_factory=CashyTask )
    options:         list[ PageOption ] = Field( default_factory=list )
    trail:           list[ NavigationStep ] = Field( default_factory=list )
    trail_start:     str = ""
    question:        Question | None = None
    paused_from:     SessionStateName | None = None
    asked_from:      SessionStateName | None = None   # where a question was asked from
    updated_at:      str = Field( default_factory=utc_now )


# -- replies -------------------------------------------------------------------

class CashyEvent( BaseModel ):
    activity: Activity | SessionStateName
    detail:   str = ""
    at:       str = Field( default_factory=utc_now )


class CashyReply( BaseModel ):
    lines:  list[ str ]
    state:  SessionStateName
    ok:     bool = True
    events: list[ CashyEvent ] = Field( default_factory=list )

    @property
    def text( self ) -> str:
        return "\n".join( self.lines )


# -- learning ------------------------------------------------------------------

WorkflowStatus = Literal[ "candidate", "verified", "retired" ]


class WorkflowVersion( BaseModel ):
    """One version of a learned banking workflow. The steps live in the
    recording `playback` names; this row says which bank, which goal, which
    version, and whether a live run has proved it."""
    id:          int = 0
    bank:        str
    goal:        str
    version:     int
    playback:    str
    status:      WorkflowStatus = "candidate"
    start_url:   str = ""
    needs:       list[ str ] = Field( default_factory=list )     # placeholders: year, month_name, ...
    note:        str = ""
    created_at:  str = Field( default_factory=utc_now )
    verified_run: int = 0


class ResolvedWorkflow( BaseModel ):
    """A way that already exists to reach a goal."""
    playback: str
    source:   Literal[ "learned", "hand-made" ]
    version:  WorkflowVersion | None = None


class ProcedureMemory( BaseModel ):
    """What LanceDB holds: a sentence about how, and a pointer to the version
    that does it. Never a step, never a page."""
    bank:     str
    goal:     str
    playback: str
    version:  int
    summary:  str
    at:       str = Field( default_factory=utc_now )


class DownloadRecord( BaseModel ):
    bank:    str
    account: str = ""                      # masked
    period:  str = ""                      # "2025-09"
    path:    str
    size:    int
    sha256:  str
    at:      str = Field( default_factory=utc_now )


class TransferHint( BaseModel ):
    """A payee in the person's own transfer records that a request may mean."""
    payee:      str
    count:      int
    accounts:   list[ str ]                # masked, as the records name them
    last_date:  str = ""
    last_amount: str = ""
    score:      float = 0.0


# -- navigating ----------------------------------------------------------------

#: How an exploration ended: on the page, stopped to ask, or out of ideas.
ExploreOutcome = Literal[ "arrived", "asked", "lost" ]


class PlaybackResult( BaseModel ):
    ok:      bool
    run_id:  int = 0
    state:   str = ""
    message: str = ""


Question.model_rebuild()
CashySession.model_rebuild()


# -- the dashboard desk --------------------------------------------------------

class DeskHandoff( BaseModel ):
    """'Put Toyota on': which dashboard window to open. The key is one of
    receptionist_team's HANDOFF_AGENTS, which the dashboard mirrors."""
    model_config = ConfigDict( frozen=True )

    agent: str
    name:  str

"""Values that cross a boundary in the bridge worker. Data only, no behaviour.

`ClaimedTask` is exactly what the gateway's claim endpoint returns -- the one
response that carries the prompt and the lease token. Every field is checked
again here, because the worker trusts the gateway's shape no more than the
gateway trusted ChatGPT's.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

NAME      = r"^[a-z][a-z0-9-]{0,40}$"
WORKSPACE = r"^[a-z][a-z0-9_-]{0,40}$"
TASK_ID   = r"^t_[0-9a-f]{24}$"
LEASE     = r"^[A-Za-z0-9_-]{20,200}$"


class ClaimedTask( BaseModel ):
    """One task this worker holds a lease on."""

    model_config = ConfigDict( extra="ignore", frozen=True )

    task_id:          str = Field( pattern=TASK_ID )
    lease_token:      str = Field( pattern=LEASE, repr=False )   # never in a log line
    lease_expires_at: int
    lease_seconds:    int = Field( ge=10, le=3600 )
    attempt:          int = Field( ge=1 )
    agent:            str = Field( pattern=NAME )
    workspace:        str = Field( pattern=WORKSPACE )
    operation:        str = Field( pattern=r"^[a-z_]{1,30}$" )
    approval_mode:    str = Field( pattern=r"^[a-z_]{1,30}$" )
    sender:           str = Field( default="chatgpt-bridge", pattern=NAME )
    prompt:           str = Field( min_length=1, max_length=8000, repr=False )


class Beat( BaseModel ):
    """The gateway's answer to a heartbeat."""

    model_config = ConfigDict( extra="ignore", frozen=True )

    cancel_requested: bool = False
    lease_expires_at: int  = 0


class OutcomeKind( str, Enum ):
    SUCCEEDED  = "succeeded"    # the agent answered
    FAILED     = "failed"       # the agent ran and failed, or could not be reached
    REFUSED    = "refused"      # this worker's policy said no; nothing ran
    CANCELLED  = "cancelled"    # the client cancelled before (or while) it ran
    LEASE_LOST = "lease_lost"   # the gateway took the task back; report nothing


class AgentOutcome( BaseModel ):
    """How one task ended, before it is published back to the gateway."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    kind:      OutcomeKind
    summary:   str = ""
    body:      str = ""
    performer: str = ""
    retryable: bool = False
    local_task_id: str | None = None    # the post-office task, for the audit trail
    details:   dict | None = None       # a write task's WriteReport, as JSON


class RunReport( BaseModel ):
    """What one worker run did -- the ADW's last line, and the tests' assertion."""

    model_config = ConfigDict( extra="forbid" )

    task_id:  str | None = None
    agent:    str | None = None
    outcome:  OutcomeKind | None = None
    state:    str | None = None          # the gateway's final state
    summary:  str = ""
    events:   list[ str ] = Field( default_factory=list )


# -- scoped writes ---------------------------------------------------------------
# What a write task reports back, as the gateway's `details` object. Every
# field says what it is: a file *saved on this machine* is not a ChatGPT
# download, and a list that was cut short says so instead of looking complete.

class WorkspaceEntry( BaseModel ):
    """One workspace in workspaces.json: a name the bridge may use, and where it lives."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    path:   str
    writes: bool = False
    checks: list[ list[ str ]] = Field( default_factory=list )   # argv lists, run jailed after a write


class WorkspaceConfig( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    write_root: str                                 # every write-enabled workspace lives under this...
    extra_write_paths: list[ str ] = Field( default_factory=list )   # ...or is named here, exactly
    workspaces: dict[ str, WorkspaceEntry ]


class WriteStart( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    baseline: str                   # git tree id of the workspace before the task
    resumed:  bool = False          # True: this task already started here once; it is not run again


class ChangedPath( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    path:   str
    status: str                     # added, modified, deleted, renamed, type_changed, copied
    old_path: str | None = None


class ChangeSet( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    paths:         list[ ChangedPath ] = Field( default_factory=list )
    diff:          str = ""
    diff_bytes:    int = 0
    diff_complete: bool = True
    diff_note:     str = ""
    local_patch:   str | None = None   # the full patch, saved on this machine (not downloadable)


class CheckRun( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    command:     list[ str ]
    exit_code:   int | None
    passed:      bool
    seconds:     float
    output_tail: str = ""


class Artifact( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    path:      str
    type:      str
    size:      int
    sha256:    str
    complete:  bool                 # True only when `content` is the whole file
    content:   str | None = None
    note:      str = ""
    retrieval: str = ""


class WriteReport( BaseModel ):
    """The `details` of a write task's result."""

    model_config = ConfigDict( extra="forbid" )

    task_id:        str
    operation:      str
    effective_mode: str
    workspace:      str
    outcome:        str             # changed, no_changes, partial, unchanged_after_failure
    summary:        str = ""
    resumed:        bool = False
    changed_paths:  list[ ChangedPath ] = Field( default_factory=list )
    diff:           ChangeSet = Field( default_factory=ChangeSet )
    checks_run:     list[ CheckRun ] = Field( default_factory=list )
    checks_not_run: list[ str ] = Field( default_factory=list )
    artifacts:      list[ Artifact ] = Field( default_factory=list )
    delivery:       str = ""


class ScanPage( BaseModel ):
    """One rendered page from the scan façade: JPEG bytes and their size in pixels."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    jpeg:   bytes = Field( repr=False )
    width:  int = Field( ge=1 )
    height: int = Field( ge=1 )


class ScanRunSettings( BaseModel ):
    """How long the scanner agent waits, and how much of a document it sends to the gateway."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    poll_seconds:          float = 3.0
    capture_deadline:      float = 300.0      # the scripts time out at 120 s; this is the outer bound
    mazda_poll_seconds:    float = 15.0
    mazda_deadline:        float = 960.0      # past the façade's 900 s "unavailable" window
    max_pages:             int = 50
    page_text_bytes:       int = 40000
    text_bytes:            int = 150000       # the gateway takes 204800
    record_bytes:          int = 32000        # the gateway takes 32768

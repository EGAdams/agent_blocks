"""The interfaces the bridge worker is written against. Protocols only.

The worker's job is the client half of the gateway's worker API plus a hand-off
to an on-machine agent. Every concrete -- HTTP, the post office, the clock --
sits behind one of these, and only `adws/adw_bridge_worker.py` names them.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from .models import AgentOutcome, Beat, ChangeSet, CheckRun, ClaimedTask, ScanPage, WriteReport, WriteStart


class GatewayError( Exception ):
    """The gateway said no. `code` is its machine-readable error code."""

    def __init__( self, status: int, code: str, message: str ) -> None:
        super().__init__( f"{ status } { code }: { message }" )
        self.status = status
        self.code   = code


class ITaskGatewayClient( Protocol ):
    """The worker side of `/api/agent-bridge/v1/worker/...`, outbound only."""

    def next( self ) -> str | None: ...                                   # a task id, or None when idle

    def claim( self, task_id: str, worker_id: str ) -> ClaimedTask | None: ...   # None: someone else got it

    def heartbeat( self, task: ClaimedTask ) -> Beat: ...                 # GatewayError 409 when the lease is gone

    def event( self, task: ClaimedTask, kind: str, message: str ) -> None: ...

    def complete( self, task: ClaimedTask, ok: bool, summary: str, body: str, performer: str,
                  details: dict | None = None ) -> str: ...   # details: a write task's report

    def fail( self, task: ClaimedTask, reason: str, retryable: bool, cancelled: bool ) -> str: ...


class IExecutionPolicy( Protocol ):
    """Says whether this worker will run a task. The gateway already decided once; this is the second lock."""

    def refusal( self, task: ClaimedTask ) -> str | None: ...             # None means allowed


class IWorkspaceResolver( Protocol ):
    """A workspace *name* to a directory. Never a path from the request."""

    def resolve( self, workspace: str ) -> Path: ...                      # LookupError when unknown


Signal = Literal[ "continue", "cancel", "lost" ]


class IAgentAdapter( Protocol ):
    """Runs one task on one on-machine agent and waits for it."""

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome: ...
    # keep_alive() is called while waiting: it heartbeats and says whether to go on,
    # stop because the client cancelled, or stop because the gateway took the lease back.


class ITaskEventSource( Protocol ):
    """The on-machine audit trail of one hand-off, as short lines safe to publish."""

    def lines( self, local_task_id: str ) -> list[ str ]: ...


class IClock( Protocol ):
    def monotonic( self ) -> float: ...

    def sleep( self, seconds: float ) -> None: ...


# -- scoped writes ----------------------------------------------------------------

class WriteBlocked( Exception ):
    """A write task cannot start here now (the workspace is busy, or an earlier write may still be running)."""


class IWorkspaceLock( Protocol ):
    """One write at a time per workspace, across worker processes."""

    def acquire( self, workspace: str ) -> None: ...                      # WriteBlocked when held elsewhere

    def release( self, workspace: str ) -> None: ...


class IChangeTracker( Protocol ):
    """What a workspace holds, and what changed in it, without touching the user's index or files."""

    def snapshot( self, workdir: Path ) -> str: ...                       # an id for the current contents

    def changes( self, workdir: Path, baseline: str ) -> ChangeSet: ...   # since `baseline`, with the full diff


class ICheckRunner( Protocol ):
    """Runs one configured check (an argv from workspaces.json, never from a task)."""

    def run( self, workdir: Path, command: list[ str ]) -> CheckRun: ...


class IWriteJournal( Protocol ):
    """Local, durable notes about write tasks: so a task never runs twice and partial edits are not lost."""

    def load( self, task_id: str ) -> dict | None: ...

    def save( self, task_id: str, record: dict ) -> None: ...

    def save_patch( self, task_id: str, patch: str ) -> str: ...          # the local path it was saved at

    def unsettled( self, workspace: str ) -> str | None: ...              # a post-office task that may still run there

    def mark_unsettled( self, workspace: str, local_task_id: str ) -> None: ...

    def clear_unsettled( self, workspace: str ) -> None: ...


class IWriteSession( Protocol ):
    """What BridgeTaskRun needs around a write task. See write_session.ScopedWriteSession."""

    def begin( self, task: ClaimedTask, workdir: Path ) -> WriteStart: ...

    def verify( self, task: ClaimedTask, workdir: Path, start: WriteStart, outcome: AgentOutcome | None ) -> object: ...

    def collect( self, task: ClaimedTask, workdir: Path, start: WriteStart, outcome: AgentOutcome | None,
                 found: object ) -> WriteReport: ...

    def end( self ) -> None: ...


# -- scanners ---------------------------------------------------------------------

class IScanFacade( Protocol ):
    """The dashboard's token-protected, loopback-only scan façade (`/api/scan-bridge/v1`).

    A scan is found only by the scan id the façade made; nothing here takes a
    path. Refusals raise GatewayError with the façade's error code.
    """

    def start( self, scanner: str, idempotency_key: str ) -> dict: ...   # the scan record, plus "replayed"

    def status( self, scan_id: str ) -> dict: ...

    def document( self, scan_id: str ) -> dict: ...                      # mime_type, size_bytes, sha256, page_count

    def original( self, scan_id: str ) -> bytes: ...

    def page( self, scan_id: str, page: int, rendition: str ) -> ScanPage: ...

    def text( self, scan_id: str, page: int ) -> dict: ...               # page, text, source, warnings

    def mazda( self, scan_id: str ) -> dict: ...                         # the full Mazda summary

    def outcomes( self, scan_id: str ) -> dict: ...                      # {"scala": {...}, "mazda": {...}} in one read


class IScanUploader( Protocol ):
    """The gateway's `/worker/scans/...` endpoints: content-addressed blobs, then one manifest."""

    def upload_blob( self, scan_id: str, sha256: str, data: bytes ) -> None: ...   # GatewayError 413 when too large

    def post_scan( self, scan_id: str, manifest: dict ) -> dict: ...                # GatewayError 409 when the lease is gone

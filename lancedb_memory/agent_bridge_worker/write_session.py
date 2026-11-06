"""ScopedWriteSession: everything a write task needs around the agent's run.

Business logic only; the git, lock, journal and check-runner concretes come in
through the interfaces in `ports.py`. The phases that call it:

    begin()   PrepareWorkspace -- lock the workspace, record a baseline, journal it
    verify()  VerifyChanges    -- what changed since the baseline, then the checks
    collect() CollectResult    -- the files, the diff, and the report the gateway stores
    end()     FinalizeTask     -- release the lock

Nothing here resets, cleans, stashes or checks out: the user's own changes are
part of the baseline, so they are never reported as the task's and never touched.
"""
from __future__ import annotations

import hashlib
import mimetypes
import time
from collections.abc import Callable
from pathlib import Path

from .models import (AgentOutcome, Artifact, ChangeSet, CheckRun, ClaimedTask, OutcomeKind, WriteReport,
                     WriteStart)
from .ports import IChangeTracker, ICheckRunner, IWorkspaceLock, IWriteJournal, WriteBlocked
from .redaction import SecretScanner

MAX_DIFF_BYTES      = 48_000      # the diff sent back; the whole patch stays on this machine
MAX_ARTIFACT_BYTES  = 48_000      # file contents sent back, all files together
MAX_FILE_BYTES      = 64_000      # one file's contents, at most
DELIVERY = ( "Files are saved on the local machine in the named workspace. Text contents are included in this "
             "result where marked complete. Nothing here is a file download in ChatGPT." )


class Verification:
    def __init__( self, changes: ChangeSet, checks_run: list[ CheckRun ], checks_not_run: list[ str ]) -> None:
        self.changes, self.checks_run, self.checks_not_run = changes, checks_run, checks_not_run


class ScopedWriteSession:
    def __init__( self, lock: IWorkspaceLock, tracker: IChangeTracker, checks: ICheckRunner, journal: IWriteJournal,
                  checks_for: Callable[[ str ], list[ list[ str ]]], settled: Callable[[ str ], bool ],
                  scanner: SecretScanner, write_problem: Callable[[ str ], str | None ] = lambda name: None ) -> None:
        self._lock       = lock
        self._tracker    = tracker
        self._checks     = checks
        self._journal    = journal
        self._checks_for = checks_for
        self._settled    = settled
        self._scan       = scanner
        self._problem    = write_problem          # re-checked per task: the directory may have changed since start-up
        self._held: str | None = None

    # -- PrepareWorkspace ---------------------------------------------------------

    def begin( self, task: ClaimedTask, workdir: Path ) -> WriteStart:
        problem = self._problem( task.workspace )
        if problem:
            raise WriteBlocked( problem )
        self._lock.acquire( task.workspace )          # WriteBlocked when another write holds it
        self._held = task.workspace
        earlier = self._journal.unsettled( task.workspace )
        if earlier is not None:
            if not self._settled( earlier ):
                self.end()
                raise WriteBlocked( f"an earlier write task in { task.workspace } may still be running on this machine "
                                    f"(post-office task { earlier }); try again when it has finished" )
            self._journal.clear_unsettled( task.workspace )
        record = self._journal.load( task.task_id )
        if record is not None:
            return WriteStart( baseline=record[ "baseline" ], resumed=True )
        baseline = self._tracker.snapshot( workdir )
        self._journal.save( task.task_id, { "task_id": task.task_id, "workspace": task.workspace, "baseline": baseline,
                                            "state": "started", "attempt": task.attempt, "at": time.time()})
        return WriteStart( baseline=baseline )

    # -- VerifyChanges --------------------------------------------------------------

    def verify( self, task: ClaimedTask, workdir: Path, start: WriteStart, outcome: AgentOutcome | None ) -> Verification:
        changes = self._tracker.changes( workdir, start.baseline )
        changes.local_patch = self._journal.save_patch( task.task_id, changes.diff ) if changes.diff else None
        changes.diff_bytes  = len( changes.diff.encode( "utf-8" ))
        self._fit_diff( changes )
        run, not_run = [], []
        configured = self._checks_for( task.workspace )
        if not configured:
            not_run.append( f"no checks are configured for { task.workspace }" )
        elif (start.resumed or outcome is None
              or outcome.kind not in ( OutcomeKind.SUCCEEDED, OutcomeKind.FAILED )
              or ( outcome.local_task_id is not None and not self._settled( outcome.local_task_id ))):
            not_run += [ f"{ ' '.join( c ) } -- skipped: execution is unsettled, cancelled, or a resumed write" for c in configured ]
        elif not changes.paths:
            not_run += [ f"{ ' '.join( c ) } -- skipped: nothing changed" for c in configured ]
        else:
            for command in configured:
                result = self._checks.run( workdir, command )
                if self._scan.found( result.output_tail ):
                    result.output_tail = "[withheld: the output looked like it held a credential]"
                run.append( result )
        return Verification( changes, run, not_run )

    # -- CollectResult ----------------------------------------------------------------

    def collect( self, task: ClaimedTask, workdir: Path, start: WriteStart, outcome: AgentOutcome | None,
                 found: Verification ) -> WriteReport:
        kind    = outcome.kind if outcome else None
        changed = bool( found.changes.paths )
        if kind is OutcomeKind.SUCCEEDED and not start.resumed:
            label = "changed" if changed else "no_changes"
        else:
            label = "partial" if changed else "unchanged_after_failure"
        report = WriteReport(
            task_id=task.task_id, operation=task.operation, effective_mode=task.approval_mode,
            workspace=task.workspace, outcome=label, summary=( outcome.summary if outcome else "" ),
            resumed=start.resumed, changed_paths=found.changes.paths, diff=found.changes,
            checks_run=found.checks_run, checks_not_run=found.checks_not_run,
            artifacts=self._artifacts( workdir, found.changes ), delivery=DELIVERY )
        record = self._journal.load( task.task_id ) or { "task_id": task.task_id, "baseline": start.baseline }
        record.update( state="finished", outcome=label, changed=[ p.path for p in found.changes.paths ], at=time.time())
        self._journal.save( task.task_id, record )
        local = outcome.local_task_id if outcome else None
        if local and not self._settled( local ):
            self._journal.mark_unsettled( task.workspace, local )
        return report

    # -- FinalizeTask -------------------------------------------------------------------

    def end( self ) -> None:
        if self._held is not None:
            self._lock.release( self._held )
            self._held = None

    # -- helpers --------------------------------------------------------------------------

    def _fit_diff( self, changes: ChangeSet ) -> None:
        kinds = self._scan.found( changes.diff )
        where = f" The full patch is saved on this machine at { changes.local_patch }." if changes.local_patch else ""
        if kinds:
            changes.diff, changes.diff_complete = "", False
            changes.diff_note = f"withheld: the diff looked like it held a credential ({ ', '.join( kinds ) }).{ where }"
        elif changes.diff_bytes > MAX_DIFF_BYTES:
            changes.diff = changes.diff.encode( "utf-8" )[ :MAX_DIFF_BYTES ].decode( "utf-8", "ignore" )
            changes.diff_complete = False
            changes.diff_note = f"incomplete: the first { MAX_DIFF_BYTES } of { changes.diff_bytes } bytes.{ where }"

    def _artifacts( self, workdir: Path, changes: ChangeSet ) -> list[ Artifact ]:
        budget, out = MAX_ARTIFACT_BYTES, []
        for changed in changes.paths:
            if changed.status == "deleted":
                continue
            path = workdir / changed.path
            if not path.is_relative_to( workdir ) or ".." in Path( changed.path ).parts:
                continue
            local = f"saved on the local machine at { path }; not downloadable from ChatGPT"
            if path.is_symlink():
                out.append( Artifact( path=changed.path, type="symlink", size=0, sha256="", complete=False,
                                      note="a symlink; not followed", retrieval=local ))
                continue
            if not path.is_file():
                continue
            data = path.read_bytes()
            kind = mimetypes.guess_type( changed.path )[ 0 ] or ( "text/markdown" if changed.path.endswith( ".md" ) else "application/octet-stream" )
            item = Artifact( path=changed.path, type=kind, size=len( data ), sha256=hashlib.sha256( data ).hexdigest(),
                             complete=False, retrieval=local )
            try:
                text = data.decode( "utf-8" )
            except UnicodeDecodeError:
                text = None
            if text is None:
                item.note = "not text; contents not included"
            elif self._scan.found( text ):
                item.note = "withheld: it looked like it held a credential"
            elif len( data ) > MAX_FILE_BYTES or len( data ) > budget:
                item.note = f"contents not included: { len( data ) } bytes is over what one result carries"
            else:
                item.content, item.complete = text, True
                item.retrieval = "the full contents are in this result (content), and the file is saved on the local machine"
                budget -= len( data )
            out.append( item )
        return out

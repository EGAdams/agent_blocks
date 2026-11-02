"""BridgeTaskRun: the worker's business logic, one method per ADW phase.

`adws/adw_bridge_worker.py` drives these methods as SSSF phases; `run_one()`
drives the same methods as ordinary code, so the ADW orchestration can be
replaced without touching anything here. Every method is safe to call after
an earlier one stopped the run -- it does nothing -- so the phase list never
needs branching.

A write task (`implement`) gets three more phases around the agent's run,
through an injected IWriteSession: PrepareWorkspace (lock it, record what it
held), VerifyChanges (what changed, then the configured checks) and
CollectResult (files, diff, report). The last two run however the agent
ended -- a timeout or a cancel after edits is reported with its changed
paths, never as "no changes". Read-only tasks never touch the session.
"""
from __future__ import annotations

from pathlib import Path

from .models import AgentOutcome, Beat, ClaimedTask, OutcomeKind, RunReport, WriteStart
from .policy import WRITE_OPERATIONS
from .ports import (GatewayError, IAgentAdapter, IClock, IExecutionPolicy, ITaskEventSource,
                    ITaskGatewayClient, IWorkspaceResolver, IWriteSession, Signal, WriteBlocked)

MAX_EVENTS = 25


class BridgeTaskRun:
    """One claim-to-finish pass for at most one task."""

    def __init__( self, gateway: ITaskGatewayClient, policy: IExecutionPolicy, workspaces: IWorkspaceResolver,
                  agents: dict[ str, IAgentAdapter ], events: ITaskEventSource, clock: IClock, worker_id: str,
                  writes: IWriteSession | None = None ) -> None:
        self._gateway    = gateway
        self._policy     = policy
        self._workspaces = workspaces
        self._agents     = agents
        self._events     = events
        self._clock      = clock
        self._worker_id  = worker_id
        self._writes     = writes
        self.write_start: WriteStart | None = None
        self._found: object | None = None
        self._write_problem = ""
        self.task:    ClaimedTask | None = None
        self.adapter: IAgentAdapter | None = None
        self.workdir: Path | None = None
        self.outcome: AgentOutcome | None = None
        self.report = RunReport()
        self._next_beat = 0.0

    @property
    def stopped( self ) -> bool:
        return self.task is None or self.outcome is not None

    # -- phases -----------------------------------------------------------------

    def claim_task( self ) -> None:
        """ClaimTask: take the next queued task's lease, or note that there was none."""
        task_id = self._gateway.next()
        if task_id is None:
            self.report.summary = "nothing queued"
            return
        self.task = self._gateway.claim( task_id, self._worker_id )
        if self.task is None:
            self.report.summary = f"{ task_id } was claimed by another worker first"
            return
        self.report.task_id, self.report.agent = self.task.task_id, self.task.agent
        self._next_beat = self._clock.monotonic() + self._beat_interval()
        self._note( "phase", f"claimed by { self._worker_id } (attempt { self.task.attempt })" )

    def resolve_agent( self ) -> None:
        """ResolveAgent: this worker's own policy, then an adapter for the agent."""
        if self.stopped:
            return
        refusal = self._policy.refusal( self.task )
        if refusal is None and self.task.agent not in self._agents:
            refusal = f"no adapter for { self.task.agent } on this worker"
        if refusal is not None:
            self.outcome = AgentOutcome( kind=OutcomeKind.REFUSED, summary=f"refused by the worker: { refusal }" )
            return
        self.adapter = self._agents[ self.task.agent ]

    def resolve_workspace( self ) -> None:
        """ResolveWorkspace: a workspace *name* to a directory on this machine."""
        if self.stopped:
            return
        try:
            self.workdir = self._workspaces.resolve( self.task.workspace )
        except LookupError as exc:
            self.outcome = AgentOutcome( kind=OutcomeKind.REFUSED, summary=str( exc ))

    def prepare_execution( self ) -> None:
        """PrepareExecution: one last cancel/lease check before anything runs."""
        if self.stopped:
            return
        signal = self._heartbeat( force=True )
        if signal == "lost":
            self.outcome = AgentOutcome( kind=OutcomeKind.LEASE_LOST )
        elif signal == "cancel":
            self.outcome = AgentOutcome( kind=OutcomeKind.CANCELLED, summary="cancelled before it started" )
        else:
            self._note( "phase", f"handing { self.task.operation } to { self.task.agent } in { self.task.workspace } "
                                 f"({ self.task.approval_mode })" )

    def prepare_workspace( self ) -> None:
        """PrepareWorkspace: for a write task, lock the workspace and record what it holds now."""
        if self.stopped or self.task.operation not in WRITE_OPERATIONS:
            return
        if self._writes is None:
            self.outcome = AgentOutcome( kind=OutcomeKind.REFUSED, summary="refused by the worker: writes are not set up here" )
            return
        try:
            self.write_start = self._writes.begin( self.task, self.workdir )
        except WriteBlocked as exc:
            self.outcome = AgentOutcome( kind=OutcomeKind.REFUSED, summary=f"not started: { exc }" )
            return
        if self.write_start.resumed:
            self.outcome = AgentOutcome( kind=OutcomeKind.FAILED, summary=(
                "this write task already started once on this machine, so it was not run again; "
                "the changed paths below are what that earlier attempt left" ))
        else:
            self._note( "phase", f"workspace { self.task.workspace } locked; baseline recorded" )

    def execute_agent( self ) -> None:
        """ExecuteAgent: hand off and wait, heartbeating, until the agent answers."""
        if self.stopped:
            return
        self.outcome = self.adapter.run( self.task, self.workdir, self._heartbeat )

    def verify_changes( self ) -> None:
        """VerifyChanges: what the task changed since its baseline, then the workspace's checks."""
        if self.task is None or self.write_start is None:
            return
        try:
            self._found = self._writes.verify( self.task, self.workdir, self.write_start, self.outcome )
        except Exception as exc:                        # noqa: BLE001 -- reported, never hidden
            self._write_problem = f"could not work out what changed: { type( exc ).__name__ }: { exc }"[ :300 ]

    def collect_result( self ) -> None:
        """CollectResult: files, diff and checks as one report, attached to the outcome."""
        if self.task is None or self.write_start is None:
            return
        if self.outcome is None:
            self.outcome = AgentOutcome( kind=OutcomeKind.FAILED, summary="the agent did not run" )
        if self._found is None:
            details = { "task_id": self.task.task_id, "operation": self.task.operation, "workspace": self.task.workspace,
                        "effective_mode": self.task.approval_mode, "outcome": "unknown",
                        "summary": self._write_problem or "the changes could not be collected",
                        "changed_paths": None, "note": "files may have changed; inspect the workspace before retrying" }
        else:
            details = self._writes.collect( self.task, self.workdir, self.write_start, self.outcome,
                                            self._found ).model_dump( mode="json" )
        self.outcome = self.outcome.model_copy( update={ "details": details })
        changed = details.get( "changed_paths" )
        self._note( "phase", f"write outcome { details[ 'outcome' ]}, "
                             f"{ 'unknown' if changed is None else len( changed )} path(s) changed" )

    def collect_events( self ) -> None:
        """CollectEvents: forward the on-machine audit trail (names and times only)."""
        if self.task is None or self.outcome is None or self.outcome.local_task_id is None:
            return
        if self.outcome.kind is OutcomeKind.LEASE_LOST:
            return
        lines = self._events.lines( self.outcome.local_task_id )[ -MAX_EVENTS: ]
        self.report.events = lines
        for line in lines:
            self._note( "log", line )

    def publish_result( self ) -> None:
        """PublishResult: tell the gateway how it ended."""
        if self.task is None or self.outcome is None:
            return
        o = self.outcome
        self.report.outcome, self.report.summary = o.kind, o.summary
        try:
            if o.kind is OutcomeKind.LEASE_LOST:
                self.report.summary = "the gateway took the lease back; nothing was published"
            elif o.kind is OutcomeKind.SUCCEEDED:
                self.report.state = self._gateway.complete( self.task, True, o.summary or "done", o.body, o.performer,
                                                            o.details )
            elif o.details is not None:     # a write task that did not finish: say what it left behind
                self.report.state = self._gateway.complete( self.task, False, self._unfinished( o ), o.body, o.performer,
                                                            o.details )
            elif o.kind is OutcomeKind.FAILED and o.body:
                self.report.state = self._gateway.complete( self.task, False, o.summary, o.body, o.performer )
            else:
                self.report.state = self._gateway.fail( self.task, o.summary or o.kind.value, o.retryable,
                                                        o.kind is OutcomeKind.CANCELLED )
        except GatewayError as exc:
            if exc.status != 409:
                raise
            self.report.summary = f"the gateway refused the result ({ exc.code }); the lease had expired"

    def finalize_task( self ) -> RunReport:
        """FinalizeTask: release the workspace, and the report the ADW prints."""
        self.close()
        return self.report

    def close( self ) -> None:
        """Release a write task's workspace lock. Safe to call more than once, and on any path."""
        if self._writes is not None:
            self._writes.end()

    def run_one( self ) -> RunReport:
        try:
            for step in ( self.claim_task, self.resolve_agent, self.resolve_workspace, self.prepare_execution,
                          self.prepare_workspace, self.execute_agent, self.verify_changes, self.collect_result,
                          self.collect_events, self.publish_result ):
                step()
        finally:
            self.close()
        return self.finalize_task()

    # -- helpers ------------------------------------------------------------------

    @staticmethod
    def _unfinished( o: AgentOutcome ) -> str:
        changed = ( o.details or {}).get( "changed_paths" )
        how     = o.summary or o.kind.value
        if "changed_paths" not in ( o.details or {}):     # not a write task (a print): nothing about files to say
            return how
        if changed is None:
            return f"{ how } -- it may have changed files; inspect the workspace before retrying"
        if changed:
            return f"{ how } -- it changed { len( changed )} file(s) before it stopped; see the changed paths"
        return f"{ how } -- no files were changed"

    def _beat_interval( self ) -> float:
        return max( 5.0, self.task.lease_seconds / 3 )

    def _heartbeat( self, force: bool = False ) -> Signal:
        now = self._clock.monotonic()
        if not force and now < self._next_beat:
            return "continue"
        self._next_beat = now + self._beat_interval()
        try:
            beat: Beat = self._gateway.heartbeat( self.task )
        except GatewayError as exc:
            if exc.status == 409:
                return "lost"
            return "continue"     # a network blip: the lease outlives a few missed beats
        return "cancel" if beat.cancel_requested else "continue"

    def _note( self, kind: str, message: str ) -> None:
        try:
            self._gateway.event( self.task, kind, message )
        except GatewayError:
            pass                  # events are best effort; the result is what matters

"""Tracing decorators for the worker: the gateway and every agent adapter, recorded as objects.

`TracingGateway` implements `ITaskGatewayClient` around the real one. A
successful claim begins one `bridge.task_run:<task>:<attempt>` object; each
heartbeat, event, completion and failure is an event on it, and a heartbeat
also reaches whichever ADW phase is running (`current_phase`), so a long
`execute_agent` phase reads as alive for as long as the lease is kept.
`TracingAgent` implements `IAgentAdapter` around one adapter and records the
`agent.<name>` object: its inputs (prompt hashed, never stored), its outcome
and its details.

Both only observe. Every call is forwarded unchanged, every result and
exception is returned or re-raised unchanged, and the tracer itself never
raises (see `object_trace.tracer`). Only `adws/adw_bridge_worker.py` wires
them in.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from object_trace.tracer import ObjectTracer, Trace

from ..models import AgentOutcome, Beat, ClaimedTask
from ..ports import IAgentAdapter, ITaskGatewayClient, Signal


def _task_inputs( task: ClaimedTask ) -> dict:
    inputs = { "agent": task.agent, "workspace": task.workspace, "operation": task.operation,
               "approval_mode": task.approval_mode, "attempt": task.attempt, "lease_seconds": task.lease_seconds,
               "prompt": task.prompt }                                    # the redactor keeps only its hash and size
    if task.agent == "notes":                                             # the diagnostic's request is a command, not a note
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if isinstance( request, dict ):
            inputs[ "request" ] = { k: request[ k ] for k in ( "notes", "marker" ) if k in request }
    return inputs


class TracingGateway:
    def __init__( self, inner: ITaskGatewayClient, tracer: ObjectTracer,
                  current_phase: Callable[[], Trace | None ] = lambda: None ) -> None:
        self._inner   = inner
        self._tracer  = tracer
        self._phase   = current_phase
        self.trace: Trace | None = None

    def next( self ) -> str | None:
        return self._inner.next()

    def supports_lanes( self ) -> bool:
        return self._inner.supports_lanes()

    def claim( self, task_id: str, worker_id: str ) -> ClaimedTask | None:
        task = self._inner.claim( task_id, worker_id )
        if task is not None:
            self.trace = self._tracer.begin(
                "bridge.task_run", f"bridge.task_run:{ task.task_id }:{ task.attempt }", interface="ITaskGatewayClient",
                implementation=type( self._inner ).__name__, module=type( self._inner ).__module__,
                label=f"{ task.agent } { task.operation } in { task.workspace }", task_id=task.task_id,
                attempt=task.attempt, heartbeat_seconds=max( 10.0, task.lease_seconds / 3 ),
                inputs={ **_task_inputs( task ), "worker_id": worker_id })
        return task

    def heartbeat( self, task: ClaimedTask ) -> Beat:
        try:
            beat = self._inner.heartbeat( task )
        except Exception as exc:
            self._on( lambda t: t.event( "error", f"heartbeat refused: { type( exc ).__name__ }: { str( exc )[ :160 ]}" ))
            raise
        note = "cancel requested" if beat.cancel_requested else "lease kept"
        self._on( lambda t: t.heartbeat( note ))
        phase = self._phase()
        if phase is not None:
            phase.heartbeat( note )
        return beat

    def event( self, task: ClaimedTask, kind: str, message: str ) -> None:
        self._inner.event( task, kind, message )
        self._on( lambda t: t.progress( f"{ kind }: { message }"[ :500 ]))

    def complete( self, task: ClaimedTask, ok: bool, summary: str, body: str, performer: str,
                  details: dict | None = None ) -> str:
        try:
            state = self._inner.complete( task, ok, summary, body, performer, details ) if details is not None \
                    else self._inner.complete( task, ok, summary, body, performer )
        except Exception as exc:
            self._on( lambda t: t.event( "error", f"complete was not accepted: { type( exc ).__name__ }: { str( exc )[ :160 ]}" ))
            raise
        outputs = { "ok": ok, "summary": summary, "body": body, "performer": performer, "gateway_state": state }
        if ok:
            self._on( lambda t: t.succeed( outputs, f"published: { state }" ))
        else:
            self._on( lambda t: t.fail( f"published as failed: { summary[ :200 ]}", outputs=outputs ))
        return state

    def fail( self, task: ClaimedTask, reason: str, retryable: bool, cancelled: bool ) -> str:
        try:
            state = self._inner.fail( task, reason, retryable, cancelled )
        except Exception as exc:
            self._on( lambda t: t.event( "error", f"fail was not accepted: { type( exc ).__name__ }: { str( exc )[ :160 ]}" ))
            raise
        outputs = { "reason": reason, "retryable": retryable, "cancelled": cancelled, "gateway_state": state }
        if cancelled:
            self._on( lambda t: ( t.output( outputs ), t.cancel( f"cancelled: { reason[ :200 ]}" )))
        else:
            self._on( lambda t: t.fail( reason[ :300 ], outputs=outputs ))
        return state

    # IScanUploader: the ScannerAgent is handed this gateway to upload a scan's pages. Bytes are never recorded.
    def upload_blob( self, scan_id: str, sha256: str, data: bytes ) -> None:
        self._inner.upload_blob( scan_id, sha256, data )
        self._on( lambda t: t.progress( f"uploaded a { len( data )}-byte page to scan { scan_id[ :40 ]}" ))

    def post_scan( self, scan_id: str, manifest: dict ) -> dict:
        answer = self._inner.post_scan( scan_id, manifest )
        self._on( lambda t: t.progress( f"posted scan { scan_id[ :40 ]}" ))
        return answer

    # IDownloadUploader: the BrowserAgent publishes a download's files for viewing. Bytes are never recorded.
    def upload_download_blob( self, task_id: str, sha256: str, data: bytes ) -> None:
        self._inner.upload_download_blob( task_id, sha256, data )
        self._on( lambda t: t.progress( f"uploaded a { len( data )}-byte download file for { task_id }" ))

    def post_downloads( self, task_id: str, manifest: dict ) -> dict:
        answer = self._inner.post_downloads( task_id, manifest )
        self._on( lambda t: t.progress( f"published { len( manifest.get( 'files' ) or [])} downloaded file(s) for viewing" ))
        return answer

    # IUploadSource: the Notes review_attach_pdf command fetches the client's uploaded PDF. Bytes are never recorded.
    def fetch_upload( self, task: ClaimedTask ) -> dict:
        try:
            answer = self._inner.fetch_upload( task )
        except Exception as exc:
            self._on( lambda t: t.event( "error", f"upload fetch refused: { type( exc ).__name__ }: { str( exc )[ :160 ]}" ))
            raise
        self._on( lambda t: t.progress( f"fetched upload { str( answer.get( 'upload_id' ))[ :40 ]} "
                                        f"({ answer.get( 'bytes' )} bytes) for { task.task_id }" ))
        return answer

    def close( self, reason: str ) -> None:
        """The run is over. A task it never published (a lost lease, a crash) is closed as such, not left 'running'."""
        if self.trace is not None and not self.trace.done:
            self.trace.cancel( f"the run ended without publishing a result: { reason[ :200 ]}" )

    def _on( self, act: Callable[[ Trace ], object ]) -> None:
        if self.trace is not None and not self.trace.done:
            act( self.trace )


class TracingAgent:
    def __init__( self, inner: IAgentAdapter, tracer: ObjectTracer, name: str ) -> None:
        self._inner  = inner
        self._tracer = tracer
        self._kind   = "agent." + re.sub( r"[^a-z0-9_]", "_", name.lower())[ :50 ]

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        impl  = getattr( self._inner, "_inner", None ) or getattr( self._inner, "_agent", None ) or self._inner
        trace = self._tracer.begin(
            self._kind, f"{ self._kind }:{ task.task_id }:{ task.attempt }", interface="IAgentAdapter",
            implementation=f"{ type( self._inner ).__name__ }" + ( f"({ type( impl ).__name__ })" if impl is not self._inner else "" ),
            module=type( self._inner ).__module__, label=f"{ task.agent } { task.operation }", task_id=task.task_id,
            attempt=task.attempt, inputs={ **_task_inputs( task ), "workdir": str( workdir )})
        try:
            outcome = self._inner.run( task, workdir, keep_alive )
        except BaseException as exc:
            trace.fail( exc ) if isinstance( exc, Exception ) else trace.cancel( f"stopped by { type( exc ).__name__ }" )
            raise
        kind = outcome.kind.value
        outputs = { "kind": kind, "summary": outcome.summary, "body": outcome.body, "performer": outcome.performer,
                    "retryable": outcome.retryable, "local_task_id": outcome.local_task_id, "details": outcome.details }
        if kind == "succeeded":
            trace.succeed( outputs, outcome.summary[ :200 ] or "succeeded" )
        elif kind in ( "cancelled", "lease_lost" ):
            trace.output( outputs )
            trace.cancel( "lease lost" if kind == "lease_lost" else "cancelled" )
        else:
            trace.fail( f"{ kind }: { outcome.summary[ :250 ]}", outputs=outputs )
        return outcome

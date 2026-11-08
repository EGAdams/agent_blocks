"""ITaskGatewayClient over HTTPS with urllib -- outbound only, no new dependency.

The worker token comes from the environment or a file outside the repository;
it is sent in the Authorization header and never written to a log, a message
or an exception. The lease token travels in request bodies for the same reason.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from ..models import Beat, ClaimedTask
from ..ports import GatewayError

# Shared hosts (HostGator's mod_security among them) answer urllib's default
# "Python-urllib/x.y" with 406. Naming the client is also simply polite.
USER_AGENT = "agent-bridge-worker/1.0 (+https://bridge.americansjewelry.com)"
MAX_BODY   = 65536
SCAN_ID    = re.compile( r"scan_[0-9A-HJKMNP-TV-Z]{26}" )
SHA256     = re.compile( r"[0-9a-f]{64}" )
TASK_ID    = re.compile( r"t_[0-9a-f]{24}" )


def clip( text: str, limit: int ) -> str:
    """Never cut silently: a clipped body says so, and how much there was."""
    if len( text ) <= limit:
        return text
    note = f"\n\n[truncated: the first { limit - 120 } of { len( text )} characters]"
    return text[ :limit - 120 ] + note


def load_worker_token( env: str = "BRIDGE_WORKER_TOKEN", file_env: str = "BRIDGE_WORKER_TOKEN_FILE" ) -> str:
    """The worker bearer token: `$BRIDGE_WORKER_TOKEN`, else the file `$BRIDGE_WORKER_TOKEN_FILE` names."""
    token = os.environ.get( env, "" ).strip()
    if not token and os.environ.get( file_env ):
        token = Path( os.environ[ file_env ]).read_text( encoding="utf-8" ).strip()
    if not token:
        raise RuntimeError( f"no worker token: set { env } or { file_env }" )
    return token


class HttpTaskGateway:
    """The gateway's `/worker/...` endpoints."""

    def __init__( self, base_url: str, token: str, timeout_seconds: float = 30.0, skip: Sequence[ str ] = () ) -> None:
        if not ( base_url.startswith( "https://" ) or base_url.startswith( "http://127.0.0.1" ) or base_url.startswith( "http://localhost" )):
            raise ValueError( "the gateway must be HTTPS (plain HTTP is allowed only on loopback)" )
        self._base    = base_url.rstrip( "/" )
        self._token   = token
        self._timeout = timeout_seconds
        self._skip    = ",".join( skip )        # a worker lane's pass-over list: `operation@workspace,...`

    def __repr__( self ) -> str:            # never show the token
        return f"HttpTaskGateway({ self._base!r })"

    # -- ITaskGatewayClient -------------------------------------------------

    def next( self ) -> str | None:
        status, body = self._call( "GET", "/worker/tasks/next" + ( f"?skip={ self._skip }" if self._skip else "" ))
        if status == 204 or not body:
            return None
        return str( body[ "task_id" ])

    def supports_lanes( self ) -> bool:
        """True when the gateway honours `?skip=`. A deliberately malformed list is refused with `bad_skip` by a gateway
        that knows lanes and silently ignored by one that does not -- and a reserved worker on the old one would claim
        tasks it must not run."""
        try:
            self._call( "GET", "/worker/tasks/next?skip=lane-probe" )
        except GatewayError as exc:
            return exc.status == 400 and exc.code == "bad_skip"
        return False

    def claim( self, task_id: str, worker_id: str ) -> ClaimedTask | None:
        try:
            _, body = self._call( "POST", f"/worker/tasks/{ task_id }/claim", { "worker_id": worker_id })
        except GatewayError as exc:
            if exc.status == 409:
                return None
            raise
        return ClaimedTask.model_validate( body )

    def heartbeat( self, task: ClaimedTask ) -> Beat:
        _, body = self._call( "POST", f"/worker/tasks/{ task.task_id }/heartbeat", { "lease_token": task.lease_token })
        return Beat.model_validate( body or {})

    def event( self, task: ClaimedTask, kind: str, message: str ) -> None:
        self._call( "POST", f"/worker/tasks/{ task.task_id }/events",
                    { "lease_token": task.lease_token, "kind": kind, "message": message[ :2000 ]})

    def complete( self, task: ClaimedTask, ok: bool, summary: str, body: str, performer: str,
                  details: dict | None = None ) -> str:
        payload = { "lease_token": task.lease_token, "ok": ok, "summary": summary[ :2000 ],
                    "body": clip( body, MAX_BODY ), "performer": performer[ :100 ]}
        if details is not None:
            payload[ "details" ] = details
        _, out = self._call( "POST", f"/worker/tasks/{ task.task_id }/complete", payload )
        return str(( out or {}).get( "state", "" ))

    def fail( self, task: ClaimedTask, reason: str, retryable: bool, cancelled: bool ) -> str:
        _, out = self._call( "POST", f"/worker/tasks/{ task.task_id }/fail",
                             { "lease_token": task.lease_token, "reason": reason[ :500 ],
                               "retryable": retryable, "cancelled": cancelled })
        return str(( out or {}).get( "state", "" ))

    # -- IScanUploader ---------------------------------------------------------

    def upload_blob( self, scan_id: str, sha256: str, data: bytes ) -> None:
        if not ( SCAN_ID.fullmatch( scan_id ) and SHA256.fullmatch( sha256 )):
            raise ValueError( "a blob is addressed by a scan id and a lowercase sha256" )
        self._send( "POST", f"/worker/scans/{ scan_id }/blobs/{ sha256 }", data, "application/octet-stream" )

    def post_scan( self, scan_id: str, manifest: dict ) -> dict:
        if not SCAN_ID.fullmatch( scan_id ):
            raise ValueError( "not a scan id" )
        data = json.dumps( manifest, ensure_ascii=False ).encode( "utf-8" )   # document text stays compact
        _, out = self._send( "POST", f"/worker/scans/{ scan_id }", data, "application/json" )
        return out or {}

    # IDownloadUploader: a browser.download task's files, then the manifest that names them.
    def upload_download_blob( self, task_id: str, sha256: str, data: bytes ) -> None:
        if not ( TASK_ID.fullmatch( task_id ) and SHA256.fullmatch( sha256 )):
            raise ValueError( "a download blob is addressed by a task id and a lowercase sha256" )
        self._send( "POST", f"/worker/downloads/{ task_id }/blobs/{ sha256 }", data, "application/octet-stream" )

    def post_downloads( self, task_id: str, manifest: dict ) -> dict:
        if not TASK_ID.fullmatch( task_id ):
            raise ValueError( "not a task id" )
        data = json.dumps( manifest, ensure_ascii=False ).encode( "utf-8" )
        _, out = self._send( "POST", f"/worker/downloads/{ task_id }", data, "application/json" )
        return out or {}

    # -- plumbing -------------------------------------------------------------

    def _call( self, method: str, path: str, payload: dict | None = None ) -> tuple[ int, dict | None ]:
        data = None if payload is None else json.dumps( payload ).encode( "utf-8" )
        return self._send( method, path, data, "application/json" )

    def _send( self, method: str, path: str, data: bytes | None, content_type: str ) -> tuple[ int, dict | None ]:
        request = urllib.request.Request( self._base + path, data=data, method=method )
        request.add_header( "Authorization", f"Bearer { self._token }" )
        request.add_header( "Accept", "application/json" )
        request.add_header( "User-Agent", USER_AGENT )
        if data is not None:
            request.add_header( "Content-Type", content_type )
        try:
            with urllib.request.urlopen( request, timeout=self._timeout ) as response:
                raw = response.read()
                return response.status, ( json.loads( raw ) if raw else None )
        except urllib.error.HTTPError as exc:
            code, message = "http_error", exc.reason or ""
            try:
                err     = json.loads( exc.read() or b"{}" ).get( "error", {})
                code    = str( err.get( "code", code ))
                message = str( err.get( "message", message ))
            except ( ValueError, AttributeError ):
                pass
            raise GatewayError( exc.code, code, message ) from None
        except urllib.error.URLError as exc:
            raise GatewayError( 0, "unreachable", str( exc.reason )) from None

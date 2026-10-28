"""SidecarTurnExecutor -- Toyota or Sydney as a recipient, through her own sidecar.

A receptionist already has an HTTP front door: `POST /turn` on her loopback
sidecar (Toyota :8767, Sydney :8769), streaming NDJSON TurnRecords. A message
for her is one turn, marked as coming from a colleague so she answers it
rather than treating it as the user. Her reply is whatever `reply` (or the
streamed `delta`s) she produced; an `error` record is a failed task.

Speaking through her sidecar rather than building her a second engine means
she answers with her own memory, routes and tools -- the same Toyota the user
talks to.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from ..models import Envelope, TaskResult


class SidecarTurnExecutor:

    def __init__( self, display_name: str, url: str, timeout_seconds: float = 600.0 ) -> None:
        self._display_name = display_name
        self._url          = url.rstrip( "/" )
        self._timeout      = timeout_seconds

    @property
    def performer( self ) -> str:
        return f"sidecar:{ self._url }"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        return await asyncio.to_thread( self._turn, envelope )

    def turn_text( self, envelope: Envelope ) -> str:
        return ( f"[Message for { self._display_name } from { envelope.sender }, through the team post office, "
                 f"task { envelope.id }. Answer it directly; do not send it back to { envelope.sender }.] "
                 f"{ envelope.text }" )

    def _turn( self, envelope: Envelope ) -> TaskResult:
        body = json.dumps({ "text": self.turn_text( envelope )[ :4000 ]}).encode( "utf-8" )
        request = urllib.request.Request( f"{ self._url }/turn", data=body, method="POST",
                                          headers={ "Content-Type": "application/json" })
        try:
            # Loopback only, so never through an http_proxy from the environment.
            opener = urllib.request.build_opener( urllib.request.ProxyHandler({}))
            with opener.open( request, timeout=self._timeout ) as response:
                lines = response.read().decode( "utf-8" ).splitlines()
        except ( urllib.error.URLError, TimeoutError, OSError ) as exc:
            return TaskResult( ok=False, performer=self.performer,
                               text=f"{ self._display_name }'s sidecar could not be reached: { exc }" )
        return self.result_from( lines )

    def result_from( self, lines: list[ str ]) -> TaskResult:
        replies, deltas, cost = [], [], None
        for line in lines:
            try:
                record = json.loads( line )
            except ValueError:
                continue
            kind = record.get( "kind" )
            if kind == "error":
                return TaskResult( ok=False, performer=self.performer, text=record.get( "text" ) or "turn failed" )
            if kind == "reply":
                replies.append( record.get( "text", "" ))
                cost = record.get( "cost_usd" ) or cost
            elif kind == "delta":
                deltas.append( record.get( "text", "" ))
        text = ( replies[ -1 ] if replies else "".join( deltas )).strip()
        if not text:
            return TaskResult( ok=False, performer=self.performer, text=f"{ self._display_name } gave no reply" )
        return TaskResult( ok=True, text=text, performer=self.performer, cost_usd=cost )

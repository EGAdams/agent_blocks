"""Adapter: one debugger event becomes one ordinary SSSF event.

GoF Adapter. `IDebugEvents.emit` is what a monitored phase speaks;
`Tracer.event` is what SSSF records. Everything the debugger needs rides in the payload under the one
key `adw_debug`, so the events table keeps its schema and every other reader of
sssf.db sees a normal row.
"""
from __future__ import annotations

from typing import Any, Protocol

from .data_types import EventRecord


class IDebugEvents( Protocol ):
    """The debugger event sink, independent of the SSSF storage adapter."""

    def emit( self, kind: str, phase_id: str = "", **fields: Any ) -> None: ...


PAYLOAD_KEY = "adw_debug"

# The SSSF event type each debugger kind is filed under. Lifecycle and progress
# are logs; a tool call and a cross-phase message already have types of their own.
_SSSF_TYPE = { "tool_call": "tool_call", "tool_result": "tool_result", "message": "handoff" }


class SssfDebugEvents:
    """Implements IDebugEvents over a Run's tracer."""

    def __init__( self, run: Any ):
        self._run = run

    def emit( self, kind: str, phase_id: str = "", **fields: Any ) -> None:
        self._run.tracer.event( EventRecord( adw_id=self._run.adw_id,
                                             phase_id=phase_id,
                                             type=_SSSF_TYPE.get( kind, "log" ),
                                             name=str( fields.get( "phase_name", kind )),
                                             payload={ PAYLOAD_KEY: { "kind": kind, **fields }}))

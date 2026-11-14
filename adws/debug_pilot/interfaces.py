"""Contracts for the debugger pilot. Nothing here imports SSSF or sqlite.

Say "interface" for these. Each one is a `typing.Protocol`, so an implementation
satisfies it by shape and a test can hand in a fake without inheriting anything.
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class FaultPlan( BaseModel ):
    """Test-only fault controls, named by step key. Empty means a clean run."""

    fail_at:       str   = ""
    stall_at:      str   = ""
    stall_seconds: float = 0.0
    kill_at:       str   = ""


class IDebugEvents( Protocol ):
    """Where one debugger event is recorded. The real one writes an SSSF event."""

    def emit( self, kind: str, phase_id: str = "", **fields: Any ) -> None: ...


class IPhaseProbe( Protocol ):
    """What a running step may report about itself. Bound to one phase instance."""

    def progress( self, text: str ) -> None: ...

    def waiting( self, reason: str, deadline_seconds: float ) -> None: ...

    def tool_call( self, tool: str, arguments: dict[ str, Any ]) -> str: ...

    def tool_result( self, call_id: str, tool: str, result: str ) -> None: ...

    def message( self, to_key: str, text: str ) -> None: ...


class IPilotStep( Protocol ):
    """One unit of pilot work. Knows nothing about SSSF, sessions or the tracer."""

    key:         str        # unique within the run: "lookup-1", "lookup-2"
    name:        str        # phase name; two steps may share one
    purpose:     str        # one sentence: what this step is responsible for
    description: str        # the phase description SSSF requires

    def perform( self, probe: IPhaseProbe ) -> None: ...

    def dispose( self ) -> None: ...


class IFaultInjector( Protocol ):
    """Asked once per step, before its work. Raises, sleeps, exits, or does nothing."""

    def before( self, key: str, probe: IPhaseProbe ) -> None: ...


class ISleeper( Protocol ):
    """Time passing. Injected so a test never really waits."""

    def sleep( self, seconds: float ) -> None: ...

"""Decorator: announce an existing SSSF phase without changing its work.

The original handle, params, result and exceptions pass through. Cleanup means
the monitor's scope closed; it makes no claim about the wrapped phase's resources.
SSSF's phase_end remains the sole source of pass/fail outcomes.
"""
from __future__ import annotations

from typing import Any

from sssf_roles import IPhase, IPhaseHandle

from .data_types import PhaseParams
from .debug_events import IDebugEvents


class MonitoredSssfPhase( IPhase ):
    def __init__( self, phase: IPhase, adw_id: str, key: str, events: IDebugEvents ):
        self._phase = phase
        self._events = events
        self._instance_id = f"{ adw_id }:{ key }"
        self._closed = False
        self._emit( "constructed", class_name=type( phase ).__name__, attempt=1 )
        self._emit( "purpose", text=phase.params.description )

    @property
    def params( self ) -> PhaseParams:
        return self._phase.params

    def act( self, handle: IPhaseHandle ) -> Any:
        phase_id = handle.phase.phase_id
        self._emit( "bound", phase_id, attempt=1 )
        try:
            result = self._phase.act( handle )
            if result is not None:
                self._emit( "progress", phase_id, text=str( result ))
            return result
        finally:
            self._close( phase_id, "monitor scope closed" )

    def close( self ) -> None:
        self._close( "", "monitor closed; never executed" )

    def _close( self, phase_id: str, note: str ) -> None:
        if not self._closed:
            self._closed = True
            self._emit( "cleanup", phase_id, result="ok", note=note )

    def _emit( self, kind: str, phase_id: str = "", **fields: Any ) -> None:
        self._events.emit( kind, phase_id, instance_id=self._instance_id,
                           phase_name=self.params.name, **fields )

"""Decorator: an IPilotStep run as an SSSF phase that reports its own lifecycle.

GoF Decorator over the step, and an Adapter to SSSF's `IPhase`. It adds three
announcements the step itself never makes -- constructed, purpose, cleanup --
and guarantees the third: `dispose()` runs in a `finally`, and `close()` covers
a step that was built but never reached. Nothing here relies on `__del__` or on
when the garbage collector runs.

Identity: `instance_id` is `<adw_id>:<step key>`, fixed at construction, before
SSSF has assigned a phase id. The `bound` event ties the two together once the
phase opens, so two phases with the same name stay two different instances.
"""
from __future__ import annotations

import uuid
from typing import Any

from adw_modules.data_types import PhaseParams

from .interfaces import IDebugEvents, IFaultInjector, IPilotStep


class PhaseProbe:
    """Implements IPhaseProbe for one bound phase instance."""

    def __init__( self, events: IDebugEvents, adw_id: str, instance_id: str, phase_id: str, phase_name: str ):
        self._events      = events
        self._adw_id      = adw_id
        self._instance_id = instance_id
        self._phase_id    = phase_id
        self._phase_name  = phase_name

    def _emit( self, kind: str, **fields: Any ) -> None:
        self._events.emit( kind, self._phase_id, instance_id=self._instance_id,
                           phase_name=self._phase_name, **fields )

    def progress( self, text: str ) -> None:
        self._emit( "progress", text=text )

    def waiting( self, reason: str, deadline_seconds: float ) -> None:
        self._emit( "waiting", text=reason, deadline_seconds=deadline_seconds )

    def tool_call( self, tool: str, arguments: dict[ str, Any ]) -> str:
        call_id = f"call_{ uuid.uuid4().hex[ :10 ]}"
        self._emit( "tool_call", tool=tool, call_id=call_id, arguments=arguments )
        return call_id

    def tool_result( self, call_id: str, tool: str, result: str ) -> None:
        self._emit( "tool_result", tool=tool, call_id=call_id, result=result )

    def message( self, to_key: str, text: str ) -> None:
        self._emit( "message", text=text, source_instance=self._instance_id,
                    target_instance=f"{ self._adw_id }:{ to_key }" )


class MonitoredPhase:
    """An IPhase. Construct it and the step is announced; run it and cleanup is certain."""

    def __init__( self,
                  step:    IPilotStep,
                  adw_id:  str,
                  events:  IDebugEvents,
                  faults:  IFaultInjector,
                  attempt: int = 1 ):
        self._step        = step
        self._adw_id      = adw_id
        self._events      = events
        self._faults      = faults
        self._attempt     = attempt
        self._instance_id = f"{ adw_id }:{ step.key }"
        self._closed      = False
        self._announce( "constructed", class_name=type( step ).__name__, attempt=attempt )
        self._announce( "purpose", text=step.purpose )

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._step.name, kind="code", owner="python",
                            description=self._step.description )

    def act( self, handle: Any ) -> None:
        phase_id = handle.phase.phase_id
        self._announce( "bound", phase_id, attempt=self._attempt )
        probe = PhaseProbe( self._events, self._adw_id, self._instance_id, phase_id, self._step.name )
        try:
            self._faults.before( self._step.key, probe )
            self._step.perform( probe )
        finally:
            self._cleanup( phase_id )

    def close( self ) -> None:
        """Cleanup for a step that never ran. A no-op once cleanup has happened."""
        self._cleanup( "", note="never executed" )

    def _cleanup( self, phase_id: str, note: str = "" ) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._step.dispose()
        except Exception as error:               # noqa: BLE001 -- reported, never masks the phase's own error
            self._announce( "cleanup", phase_id, result="error", error=str( error ), note=note )
        else:
            self._announce( "cleanup", phase_id, result="ok", note=note )

    def _announce( self, kind: str, phase_id: str = "", **fields: Any ) -> None:
        self._events.emit( kind, phase_id, instance_id=self._instance_id,
                           phase_name=self._step.name, **fields )

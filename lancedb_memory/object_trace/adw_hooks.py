"""AdwRunTrace: one ADW run, and each of its phases, as traced objects.

    with AdwRunTrace( tracer, "adw_bridge_worker", adw_id ) as hooks:
        for name, description, act in phases( svc ):
            executor.execute( run, ServicePhase( name, description, hooks.phase( name, act ), log ))

A phase depends on the phase before it, so the inspector can draw the chain
and say which link a run stopped at. `task_id_of` lets a phase pick up a task
id that only exists once an earlier phase has claimed it. Nothing here
changes what the phase does or returns; an exception is recorded and
re-raised unchanged.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .tracer import ObjectTracer, Trace, current_scope, safe_id, trace_scope


class AdwRunTrace:
    def __init__( self, tracer: ObjectTracer, adw_name: str, adw_id: str | None = None, *, inputs: object = None,
                  task_id_of: Callable[[], str | None ] = lambda: None, phase_heartbeat_seconds: float | None = None ) -> None:
        self._tracer   = tracer
        self._name     = adw_name
        self.adw_id    = safe_id( adw_id ) if adw_id else uuid.uuid4().hex[ :8 ]
        self._inputs   = inputs
        self._task_of  = task_id_of
        self._beat     = phase_heartbeat_seconds
        self.run: Trace | None = None
        self.current: Trace | None = None
        self._previous: str | None = None
        self._scope    = None
        self._closers: list[ Callable[[ str ], None ]] = []

    def __enter__( self ) -> "AdwRunTrace":
        self._scope = trace_scope( adw_id=self.adw_id, run_id=current_scope().run_id or self.adw_id )
        self._scope.__enter__()
        self.run = self._tracer.begin( "adw.run", f"adw.run:{ self.adw_id }", implementation=self._name,
                                       module=self._name, label=self._name, inputs=self._inputs, adw_id=self.adw_id )
        return self

    def __exit__( self, exc_type, exc, tb ) -> bool:
        reason = f"{ type( exc ).__name__ }: { str( exc )[ :160 ]}" if exc is not None else "finished"
        for close in self._closers:
            try:
                close( reason )
            except Exception:                 # noqa: BLE001 -- closing a record must not mask the run's own outcome
                pass
        try:
            if self.run is not None and not self.run.done:
                if exc is not None:
                    self.run.fail( exc )
                else:
                    self.run.succeed( outputs={ "scope": "orchestration", "task_outcome": "see agent/task objects" },
                                      message=f"{ self._name } orchestration finished; task outcome is recorded separately" )
        finally:
            if self._scope is not None:
                self._scope.__exit__( exc_type, exc, tb )
        return False

    def phase( self, name: str, act: Callable[[], Any ], description: str = "" ) -> Callable[[], Any ]:
        def traced() -> Any:
            task_id = _quiet( self._task_of )
            deps    = [ self._previous ] if self._previous else []
            trace   = self._tracer.begin( "adw.phase", f"adw.phase:{ self.adw_id }:{ name }", implementation=self._name,
                                          module=self._name, label=description or name, phase=name, deps=deps,
                                          parent_id=self.run.object_id if self.run else None, task_id=task_id,
                                          heartbeat_seconds=self._beat )
            self.current, self._previous = trace, trace.object_id
            try:
                out = act()
            except BaseException as exc:
                trace.fail( exc ) if isinstance( exc, Exception ) else trace.cancel( f"stopped by { type( exc ).__name__ }" )
                raise
            finally:
                self.current = None
            trace.succeed( message=f"{ name } done" )
            return out
        return traced

    def closing( self, close: Callable[[ str ], None ]) -> None:
        """Call `close( reason )` when the run ends -- for objects begun inside it that might never finish."""
        self._closers.append( close )

    def note( self, message: str ) -> None:
        if self.run is not None:
            self.run.progress( message )


def _quiet( read: Callable[[], str | None ]) -> str | None:
    try:
        value = read()
    except Exception:                         # noqa: BLE001
        return None
    return safe_id( value ) if value else None

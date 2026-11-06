"""Object tracing for every SSSF ADW, from the one executor they all share.

`adws/sssf_roles.py` hands every interface-style ADW its
`ManualContextPhaseExecutor`. `traced_executor( Base )` returns a subclass of
that executor with the same constructor, so swapping it in there traces
each ADW's run and phases without touching the ADWs themselves:

    adw.run:<adw_id>                 the SSSF run, keyed by SSSF's own adw_id so the two traces line up
    adw.phase:<adw_id>:<phase>       one per phase, depending on the phase before it

The run closes when `run.finish()` is called (accepted or not), or, if the
process exits first, as failed with "exited without finishing". A phase
re-raises whatever it raised; nothing here changes what a phase does or
returns. Inside an `AdwRunTrace` (an ADW that traces itself, like
adw_bridge_worker) the executor stays out of the way, so nothing is traced
twice. Every telemetry failure is swallowed: the ADW runs as before.
"""
from __future__ import annotations

import atexit
import sys
import weakref
from pathlib import Path
from typing import Any

from .factory import default_tracer
from .tracer import Trace, current_scope, safe_id, trace_scope

EXITED = "the process exited without finishing the run"


class _RunTraces:
    """The live adw.run trace for each SSSF Run object, and the last phase begun in it."""

    def __init__( self ) -> None:
        self._runs: "weakref.WeakKeyDictionary[ Any, dict[ str, Any ]]" = weakref.WeakKeyDictionary()
        self._open: list[ Trace ] = []
        atexit.register( self._close_all )

    def of( self, run: Any ) -> dict[ str, Any ] | None:
        entry = self._runs.get( run )
        if entry is not None:
            return entry
        adw_id = safe_id( str( getattr( run, "adw_id", "" ) or "" )) or None
        if adw_id is None:
            return None
        name  = Path( sys.argv[ 0 ] or "adw" ).stem
        trace = default_tracer().begin( "adw.run", f"adw.run:{ adw_id }", interface="IApplication",
                                        implementation=name, module=name, label=name, adw_id=adw_id,
                                        inputs={ "argv": [ a[ :80 ] for a in sys.argv[ 1:12 ]]})
        entry = { "adw_id": adw_id, "trace": trace, "previous": None, "names": {}}
        self._runs[ run ] = entry
        self._open.append( trace )
        self._wrap_finish( run, trace )
        return entry

    @staticmethod
    def _wrap_finish( run: Any, trace: Trace ) -> None:
        finish = getattr( run, "finish", None )
        if not callable( finish ):
            return

        def traced_finish( accepted: bool = True, reason: str = "", *args: Any, **kwargs: Any ) -> Any:
            out = finish( accepted, reason, *args, **kwargs )
            try:
                if not trace.done:
                    if accepted:
                        trace.succeed( message=( reason or "finished" )[ :200 ])
                    else:
                        trace.fail( ( reason or "not accepted" )[ :300 ])
            except Exception:                 # noqa: BLE001 -- telemetry never changes the run's result
                pass
            return out
        try:
            run.finish = traced_finish
        except Exception:                     # noqa: BLE001 -- a Run we cannot wrap closes at exit instead
            pass

    def _close_all( self ) -> None:
        for trace in self._open:
            try:
                if not trace.done:
                    trace.fail( EXITED )
            except Exception:                 # noqa: BLE001
                pass


_RUNS = _RunTraces()


def traced_executor( base: type ) -> type:
    """A subclass of the SSSF phase executor `base` that traces each run and phase it executes."""

    class TracedPhaseExecutor( base ):        # type: ignore[ misc, valid-type ]
        __doc__ = f"{ base.__name__ }, with each run and phase recorded in the object trace."

        def execute( self, run: Any, phase: Any ) -> Any:
            if current_scope().adw_id is not None:          # an AdwRunTrace already covers this ADW
                return super().execute( run, phase )
            entry, trace = None, None
            try:
                entry = _RUNS.of( run )
                if entry is not None:
                    trace = _begin_phase( entry, phase )
            except Exception:                 # noqa: BLE001
                trace = None
            if trace is None:
                return super().execute( run, phase )
            with trace_scope( adw_id=entry[ "adw_id" ]):
                try:
                    out = super().execute( run, phase )
                except BaseException as exc:
                    try:
                        trace.fail( exc ) if isinstance( exc, Exception ) else trace.cancel( f"stopped by { type( exc ).__name__ }" )
                    except Exception:         # noqa: BLE001
                        pass
                    raise
            try:
                trace.succeed( message=f"{ trace.object_id.rsplit( ':', 1 )[ -1 ]} done" )
            except Exception:                 # noqa: BLE001
                pass
            return out

    TracedPhaseExecutor.__name__ = TracedPhaseExecutor.__qualname__ = base.__name__
    return TracedPhaseExecutor


def _begin_phase( entry: dict[ str, Any ], phase: Any ) -> Trace:
    params = getattr( phase, "params", None )
    name   = safe_id( str( getattr( params, "name", "" ) or type( phase ).__name__ ))
    seen   = entry[ "names" ][ name ] = entry[ "names" ].get( name, 0 ) + 1
    pid    = f"adw.phase:{ entry[ 'adw_id' ]}:{ name }" + ( f":{ seen }" if seen > 1 else "" )
    run    = entry[ "trace" ]
    trace  = default_tracer().begin( "adw.phase", pid, interface="IPhase", implementation=type( phase ).__name__,
                                     module=type( phase ).__module__, label=str( getattr( params, "description", "" ) or name )[ :160 ],
                                     phase=name, deps=[ entry[ "previous" ]] if entry[ "previous" ] else [],
                                     parent_id=run.object_id, adw_id=entry[ "adw_id" ],
                                     inputs={ "owner": getattr( params, "owner", None ), "kind": getattr( params, "kind", None )})
    entry[ "previous" ] = trace.object_id
    return trace

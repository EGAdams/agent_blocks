"""A receptionist turn as a traced object (object_trace), so the debugger and the
inspector can say which turn a Bridge task came from.

turn_runner wraps each turn in traced_turn(); everything begun inside it -- the
Bridge submission bridge_client/tracing.py records, for one -- inherits the
turn's request id as its run_id. Telemetry only: if object_trace is missing or
its store is broken, the turn runs exactly as before (object_trace never
raises from a write; a missing package means no trace at all).
"""
import contextlib
from collections.abc import Iterator

TURN_KIND = "receptionist.turn"


@contextlib.contextmanager
def traced_turn(agent: str, request_id: str, text: str) -> Iterator[object | None]:
    try:
        from object_trace.factory import default_tracer
        from object_trace.tracer import trace_scope
        tracer = default_tracer()
    except Exception:  # noqa: BLE001 -- no tracing package or store is no reason to refuse a turn
        yield None
        return
    with trace_scope(run_id=request_id):
        trace = tracer.begin(TURN_KIND, f"{TURN_KIND}:{request_id}", interface="ReceptionistTurn",
                             implementation="_TurnRunner", module="receptionist_team.turn_runner",
                             label=f"{agent} turn", inputs={"agent": agent, "chars": len(text or ""),
                                                             "text": (text or "")[:160]})
        try:
            yield trace
        except BaseException as exc:
            trace.fail(exc)
            raise
        if not trace.done:
            trace.succeed(message="turn finished")


def end_turn(trace: object | None, ok: bool) -> None:
    """A turn that ended in an error record failed, even though nothing raised."""
    if trace is not None and not ok and not trace.done:
        trace.fail("the turn ended with an error record")

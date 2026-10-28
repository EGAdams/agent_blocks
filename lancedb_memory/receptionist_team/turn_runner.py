"""Serialized turn execution and Observer relays, independent of HTTP transport."""
import asyncio
import queue
import threading
from time import perf_counter
from typing import Any, Iterator

from agent_memory.config import Settings
from agent_memory.errors import ModelUnavailable
from agent_memory.adapters.turn_telemetry import turn_scope, record_timing
from .adapters.reply_stream import PublicReplyRelay
from .alert_inbox import IAlertInbox, NoAlerts
from .main import build_mazda_desk, build_receptionist, build_shared_memory
from .profiles import TOYOTA, ReceptionistProfile
from .service_models import MemoryComposeRequest, MemoryRecordRequest, TurnRecord, TurnRequest

_DONE = object()

class _DelegationRelay:
    """Single-slot sink for the acknowledgment of whichever turn is running.

    RoutingModel calls this the moment it matches a ROUTE line, before the
    slow run_role, so the browser can start speaking "please wait while I
    contact..." while the finance role works. Turns are serialized by
    _TurnRunner's lock, so only one sink is ever bound.
    """

    def __init__(self) -> None:
        self._sink = None

    def bind(self, sink) -> None:
        self._sink = sink

    def unbind(self) -> None:
        self._sink = None

    def __call__(self, sentence: str) -> None:
        if self._sink is not None:
            self._sink(sentence)


class _TurnRunner:
    """Owns the warm agent, its event loop, and the one-turn-at-a-time lock."""

    def __init__(
        self,
        agent,
        cost,
        settings: Settings,
        role_model: str,
        relay: _DelegationRelay,
        work_relay: _DelegationRelay | None = None,
        handoff_relay: _DelegationRelay | None = None,
        memory_desk=None,
        profile: ReceptionistProfile = TOYOTA,
        reply_relay: PublicReplyRelay | None = None,
        alerts: IAlertInbox | None = None,
    ) -> None:
        self._agent = agent
        self._cost = cost
        self._settings = settings
        self._role_model = role_model
        self._relay = relay
        self._work_relay = work_relay or _DelegationRelay()
        self._handoff_relay = handoff_relay or _DelegationRelay()
        self._memory_desk = memory_desk
        self._profile = profile
        self._reply_relay = reply_relay or PublicReplyRelay()
        self._alerts = alerts or NoAlerts()
        self._loop = asyncio.new_event_loop()
        self._lock = asyncio.Lock()
        threading.Thread(
            target=self._run_loop, name=f"{profile.name}-turns", daemon=True
        ).start()

    @classmethod
    def build(
        cls, settings: Settings, role_model: str, profile: ReceptionistProfile = TOYOTA
    ) -> "_TurnRunner":
        """The real composition: one warm receptionist, wired to stream her acks.

        Separate from __init__ so tests can drive the turn loop with a fake
        agent instead of loading torch and opening a LanceDB table. `profile`
        defaults to Toyota because the sidecar was hers before Mazda had one.
        """
        relay = _DelegationRelay()
        work_relay = _DelegationRelay()
        handoff_relay = _DelegationRelay()
        reply_relay = PublicReplyRelay()
        memory = build_shared_memory(settings)
        agent, cost = build_receptionist(
            profile, settings, role_model, on_delegation=relay, on_work=work_relay,
            on_handoff=handoff_relay, memory=memory, on_reply_delta=reply_relay,
        )
        runner: list["_TurnRunner"] = []
        # Only Toyota hosts a guest's memory: the dashboard can still run Mazda
        # as a Letta agent, whose turns happen outside this package and would
        # otherwise have no recall. Her recall reads Toyota's CURRENT session,
        # which rotates on compact -- hence the callable, not a fixed string.
        desk = (
            build_mazda_desk(*memory, toyota_session=lambda: runner[0].session_id)
            if profile.serves_peer_memory
            else None
        )
        runner.append(cls(
            agent, cost, settings, role_model, relay, work_relay, handoff_relay, desk,
            profile=profile, reply_relay=reply_relay,
            alerts=profile.build_alerts() if profile.build_alerts else None,
        ))
        return runner[0]

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def session_id(self) -> str:
        return self._settings.session_id

    @property
    def display_name(self) -> str:
        return self._profile.display_name

    def compose_memory(self, request: MemoryComposeRequest) -> dict[str, Any]:
        """Recalled context for another agent's turn. Takes no turn lock: it
        reads and writes a different session, so it can't break this
        receptionist's recall-before-persist ordering, and the guest never
        waits on a delegation."""
        if self._memory_desk is None:
            return {"ok": False, "error": "shared memory is not configured"}
        future = asyncio.run_coroutine_threadsafe(
            self._memory_desk.compose(request.session, request.text), self._loop)
        prompt, recalled = future.result()
        return {"ok": True, "prompt": prompt, "recalled": recalled}

    def record_memory(self, request: MemoryRecordRequest) -> dict[str, Any]:
        if self._memory_desk is None:
            return {"ok": False, "error": "shared memory is not configured"}
        future = asyncio.run_coroutine_threadsafe(
            self._memory_desk.record(request.session, request.user, request.agent), self._loop)
        future.result()
        return {"ok": True}

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "session": self._settings.session_id,
            "model": self._settings.model,
            "role_model": self._role_model,
            "agent": self._profile.name,
            "display_name": self._profile.display_name,
            "embedding_profile": self._settings.embedding_profile,
            "total_cost_usd": round(self._cost.total_cost_usd, 6),
            "shared_memory": self._memory_desk is not None,
        }

    def alerts(self) -> dict[str, Any]:
        """Failed tasks she sent and hasn't told anyone about. Never raises:
        a broken alert board must not take her /alerts down with it silently,
        so it answers ok=False with the reason instead."""
        try:
            return {"ok": True, "agent": self._profile.name, "alerts": self._alerts.pending()}
        except Exception as exc:
            return {"ok": False, "agent": self._profile.name, "alerts": [],
                    "error": f"{type(exc).__name__}: {exc}"}

    def acknowledge_alerts(self, alert_ids: list[str] | None) -> dict[str, Any]:
        try:
            return {"ok": True, "acknowledged": self._alerts.acknowledge(alert_ids)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _announce_alerts(self, outbox: queue.Queue) -> None:
        """Say any failure out loud before this turn's answer, then mark it heard.

        An acknowledgment record is spoken the moment it arrives, so the user
        hears it even if her reply is about something else. Failing to read
        the board is announced too -- it is the one place a silent failure
        would otherwise hide.
        """
        state = self.alerts()
        if not state["ok"]:
            outbox.put(TurnRecord(kind="acknowledgment",
                                  text=f"I can't read my failure alerts right now: {state['error']}"))
            return
        if not state["alerts"]:
            return
        lines = " ".join(alert["spoken"] for alert in state["alerts"])
        outbox.put(TurnRecord(kind="acknowledgment", text=f"Before I answer, something went wrong. {lines}"))
        self.acknowledge_alerts([alert["id"] for alert in state["alerts"]])

    def clear_context(self) -> dict[str, Any]:
        """The dashboard's "compact conversation" button.

        Routed through the same lock a turn uses, so it can't land between a
        running turn's recall and its persist -- see _turn()'s docstring on
        why that ordering matters. Updates self._settings too, so /health and
        the next turn's TurnRecord.session agree with what the agent now uses.
        """
        future = asyncio.run_coroutine_threadsafe(self._clear_context(), self._loop)
        return future.result()

    async def _clear_context(self) -> dict[str, Any]:
        async with self._lock:
            new_session = self._agent.clear_context()
            self._settings = self._settings.model_copy(update={"session_id": new_session})
            return {"ok": True, "session": new_session}

    def compact(self) -> dict[str, Any]:
        """The dashboard's "Compact Conversation" button: her model summarizes
        the session, and the next turn starts from that summary.

        Same lock as a turn, for the same reason as clear_context(). Fails
        closed: if the summary can't be written the session is unchanged.
        """
        future = asyncio.run_coroutine_threadsafe(self._compact(), self._loop)
        return future.result()

    async def _compact(self) -> dict[str, Any]:
        async with self._lock:
            try:
                result = await self._agent.compact()
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._settings = self._settings.model_copy(update={"session_id": result.session_id})
            return {"ok": True, "session": result.session_id, "summary": result.summary,
                    "turns_before": result.turns_before}

    def stream(self, request: TurnRequest) -> Iterator[TurnRecord]:
        """Yield this turn's records as they happen, on the caller's thread.

        The turn itself runs on the agent's event loop; records arrive through
        a queue so an acknowledgment can reach the client while handle_turn is
        still blocked inside a delegation.
        """
        outbox: queue.Queue = queue.Queue()
        asyncio.run_coroutine_threadsafe(self._turn(request, outbox), self._loop)
        while True:
            item = outbox.get()
            if item is _DONE:
                return
            yield item

    async def _turn(self, request: TurnRequest, outbox: queue.Queue) -> None:
        """Serialize a turn and record queue time separately from execution."""
        with turn_scope(request.request_id):
            queued = perf_counter()
            async with self._lock:
                record_timing('queue_wait', perf_counter() - queued)
                started = perf_counter()
                ok = await self._run_turn(request, outbox)
                record_timing('turn', perf_counter() - started, 'ok' if ok else 'error')

    async def _run_turn(self, request: TurnRequest, outbox: queue.Queue) -> bool:
        self._reply_relay.bind(lambda text: outbox.put(TurnRecord(kind='delta', text=text)))
        self._work_relay.bind(lambda work: outbox.put(TurnRecord(kind="work", work=work)))
        self._handoff_relay.bind(
            lambda handoff: outbox.put(TurnRecord(kind="handoff", handoff=handoff)))
        self._relay.bind(
            lambda sentence: outbox.put(TurnRecord(kind="acknowledgment", text=sentence)))
        try:
            await asyncio.to_thread(self._announce_alerts, outbox)
            reply = await self._agent.handle_turn(request.text, request_id=request.request_id)
            outbox.put(TurnRecord(kind="reply", text=reply.text, cost_usd=reply.cost_usd,
                                  streamed=self._reply_relay.delivered))
            outbox.put(TurnRecord(kind="result", session=self._settings.session_id,
                                  cost_usd=round(self._cost.total_cost_usd, 6)))
            return True
        except Exception as exc:
            print(f"[{self._profile.name}-service] turn failed: {type(exc).__name__}: {exc}", flush=True)
            outbox.put(TurnRecord(kind="error", text=str(exc) if isinstance(exc, ModelUnavailable)
                                 else f"{type(exc).__name__}: {exc}"))
            return False
        finally:
            self._work_relay.unbind()
            self._handoff_relay.unbind()
            self._relay.unbind()
            self._reply_relay.unbind()
            outbox.put(_DONE)

"""Serialized turn execution and Observer relays, independent of HTTP transport."""
import asyncio
import queue
import threading
from typing import Any, Iterator

from agent_memory.config import Settings
from .main import build_toyota
from .service_models import TurnRecord, TurnRequest

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
    ) -> None:
        self._agent = agent
        self._cost = cost
        self._settings = settings
        self._role_model = role_model
        self._relay = relay
        self._work_relay = work_relay or _DelegationRelay()
        self._loop = asyncio.new_event_loop()
        self._lock = asyncio.Lock()
        threading.Thread(
            target=self._run_loop, name="toyota-turns", daemon=True
        ).start()

    @classmethod
    def build(cls, settings: Settings, role_model: str) -> "_TurnRunner":
        """The real composition: one warm Toyota, wired to stream her acks.

        Separate from __init__ so tests can drive the turn loop with a fake
        agent instead of loading torch and opening a LanceDB table.
        """
        relay = _DelegationRelay()
        work_relay = _DelegationRelay()
        agent, cost = build_toyota(settings, role_model, on_delegation=relay, on_work=work_relay)
        return cls(agent, cost, settings, role_model, relay, work_relay)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "session": self._settings.session_id,
            "model": self._settings.model,
            "role_model": self._role_model,
            "embedding_profile": self._settings.embedding_profile,
            "total_cost_usd": round(self._cost.total_cost_usd, 6),
        }

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
        """Run one turn, reporting everything through the outbox.

        Deliberately raises nothing: a failed turn is an error record, so the
        caller has exactly one way to learn how the turn ended.
        """
        async with self._lock:
            self._work_relay.bind(lambda work: outbox.put(TurnRecord(kind="work", work=work)))
            self._relay.bind(
                lambda sentence: outbox.put(
                    TurnRecord(kind="acknowledgment", text=sentence)
                )
            )
            try:
                reply = await self._agent.handle_turn(request.text, request_id=request.request_id)
                outbox.put(
                    TurnRecord(kind="reply", text=reply.text, cost_usd=reply.cost_usd)
                )
                outbox.put(
                    TurnRecord(
                        kind="result",
                        session=self._settings.session_id,
                        cost_usd=round(self._cost.total_cost_usd, 6),
                    )
                )
            except Exception as exc:
                print(f"[toyota-service] turn failed: {type(exc).__name__}: {exc}")
                outbox.put(
                    TurnRecord(kind="error", text=f"{type(exc).__name__}: {exc}")
                )
            finally:
                self._work_relay.unbind()
                self._relay.unbind()
                outbox.put(_DONE)



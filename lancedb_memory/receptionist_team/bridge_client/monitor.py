"""Status-derived bounded lifecycle monitoring, adapted from the staged monitor."""
import asyncio
from collections import deque
from dataclasses import dataclass

from .models import BridgeEvent, BridgeTaskView
from .interfaces import IBridgeClient


@dataclass(frozen=True)
class MonitorState:
    task_id: str
    state: str
    done: bool
    events_available: bool
    recent_events: tuple[BridgeEvent, ...]
    events_note: str


class TaskMonitor:
    def __init__(self, task_id: str, client: IBridgeClient) -> None:
        self.task_id = task_id
        self._client = client
        self._events: deque[BridgeEvent] = deque(maxlen=50)
        self._last_seq = 0

    def observe(self, view: BridgeTaskView) -> MonitorState:
        if view.task_id != self.task_id:
            raise ValueError("task id mismatch")
        for event in sorted(view.recent_events or (), key=lambda e: e.seq):
            if event.seq > self._last_seq:
                self._events.append(event)
                self._last_seq = event.seq
        return MonitorState(self.task_id, view.state, view.terminal, view.events_available,
                            tuple(self._events), "" if view.events_available else "Lifecycle events unavailable in status response.")

    def poll_sync(self) -> MonitorState:
        return self.observe(self._client.status(self.task_id))

    async def poll(self) -> MonitorState:
        # Unlike the staged prototype, the synchronous HTTP call actually runs off-loop.
        return await asyncio.to_thread(self.poll_sync)

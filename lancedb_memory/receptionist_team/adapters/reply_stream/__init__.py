"""Per-turn public text gate. Control instructions never reach speech or the UI."""
from collections.abc import Callable
from time import perf_counter

from agent_memory.adapters.turn_telemetry import record_timing


class PublicReplyRelay:
    def __init__(self) -> None:
        self._sink: Callable[[str], None] | None = None
        self._buffer = ''
        self._public = False
        self.delivered = False

    def bind(self, sink: Callable[[str], None]) -> None:
        self._sink, self._buffer = sink, ''
        self._public, self.delivered = False, False
        self._started = perf_counter()

    def unbind(self) -> None:
        self._sink = None
        self._buffer = ''

    def __call__(self, delta: str) -> None:
        if self._sink is None:
            return
        if not self._public:
            self._buffer += delta
            text = self._buffer.lstrip()
            if not text or any(prefix.startswith(text) or text.startswith(prefix)
                               for prefix in ('ROUTE:', 'WORK:', 'OPEN_AGENT:')):
                return
            self._public = True
            delta, self._buffer = self._buffer, ''
        # Browser speech/event gates intentionally reject blank events. Keep
        # separators with the next word so token boundaries cannot join words.
        if not delta.strip():
            self._buffer += delta
            return
        delta, self._buffer = self._buffer + delta, ''
        if not self.delivered:
            record_timing('first_reply_text', perf_counter() - self._started)
        self.delivered = True
        self._sink(delta)

"""Fakes for the relay's interfaces: a browser server that never existed, and a clock that never waits.

The point of these is that every test here runs with no network, no Chrome and no ChatGPT account -- the
relay's rules (the guard chain, the snapshot, the settling, the refusals) are the part worth pinning, and
none of them needs a real page.
"""
from __future__ import annotations

from chatgpt_relay.errors import RelayUnreachable
from chatgpt_relay.models import RelayInstance, SettleReport, ThreadView
from chatgpt_relay.transport import HttpRelayTransport


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class FakeTransport:
    """A browser server in a dict, answering in the real server's vocabulary.

    "opened", "typed", "sent" are what `browser_server.py` actually says; a fake that answered "ok"
    everywhere would have let the relay's first live bug through its own tests.
 `script` is what each successive /read_thread returns, as the server's
    own JSON shape, so `HttpRelayTransport.to_thread` (the real parser) is what builds the ThreadView."""

    def __init__(self, script: list[dict] | None = None, health: dict | None = None,
                 unreachable: set[str] | None = None, type_status: str = "typed",
                 send_status: str = "sent") -> None:
        self.script = list(script or [])
        self.health_body = health or {"status": "ok", "browser_started": True}
        self.unreachable = set(unreachable or ())
        self.type_status, self.send_status = type_status, send_status
        self.calls: list[str] = []
        self.typed: list[str] = []
        self.reads = 0

    def _maybe_fail(self, what: str) -> None:
        self.calls.append(what)
        if what in self.unreachable:
            raise RelayUnreachable(f"{what} did not answer")

    def health(self, instance: RelayInstance) -> dict:
        self._maybe_fail("health")
        return dict(self.health_body)

    def open(self, instance: RelayInstance, url: str) -> dict:
        self._maybe_fail("open")
        return {"status": "opened", "url": url}

    def new_chat(self, instance: RelayInstance) -> dict:
        self._maybe_fail("new_chat")
        return {"status": "opened", "url": "https://chatgpt.com/"}

    def type(self, instance: RelayInstance, text: str) -> dict:
        self._maybe_fail("type")
        self.typed.append(text)
        return {"status": self.type_status}

    def send(self, instance: RelayInstance) -> dict:
        self._maybe_fail("send")
        return {"status": self.send_status, "method": "enter"}

    def read_thread(self, instance: RelayInstance, last: int | None = None) -> ThreadView:
        self._maybe_fail("read_thread")
        body = self.script[min(self.reads, len(self.script) - 1)] if self.script else {"thread": [], "turn_count": 0}
        self.reads += 1
        return HttpRelayTransport.to_thread(body)


def thread(*turns: tuple[str, str], count: int | None = None) -> dict:
    """`thread(("user", "hi"), ("assistant", "hello"))` as the server's JSON."""
    rows = [{"role": role, "turn_id": f"t{i}", "text": text} for i, (role, text) in enumerate(turns, start=1)]
    return {"thread": rows, "turn_count": count if count is not None else len(rows)}

"""IRelayTransport: the browser server, and the Decorators wrapped around it.

`HttpRelayTransport` is an Adapter and a remote Proxy at once -- it adapts letta-code's
`browser_tools/browser_server.py` (a Flask app whose vocabulary is /type, /send, /read_thread) to this
package's interface, and it stands in for a Chrome that is on another machine entirely.

`RetryingTransport` and `ObservedTransport` are Decorators: same interface, wrapped instance, one added
concern each. They are deliberately separate so a test can take the retry off and keep the observing.

stdlib only. `urllib` is enough for seven endpoints, and the one interpreter this has to run under
(`lancedb_memory/.venv`, and `uv run` for the ADW) should not grow a dependency for that.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .errors import RelayUnreachable
from .interfaces import IRelayClock, IRelayObserver, IRelayTransport
from .models import RelayInstance, RelayTurn, ThreadView

#: A read of the thread, or a health check: quick, and worth giving up on quickly.
DEFAULT_TIMEOUT = 20.0
#: Typing, opening and starting a chat are not quick. The server drives a real Chrome through WebDriver
#: and types character by character, so a paragraph takes the better part of a minute -- measured: a
#: 63-character message timed out at 20s on the first live run of this relay. A short timeout here does
#: not make the relay faster, it makes it report a working relay as unreachable.
SLOW_TIMEOUT = 150.0
#: The calls that get SLOW_TIMEOUT.
SLOW = ("/type", "/open", "/new_chat", "/new-chat")
MAX_THREAD_TURNS = 200


class HttpRelayTransport:
    """IRelayTransport over the browser server's HTTP API.

    Every call returns the server's decoded JSON, or raises RelayUnreachable. A non-2xx answer is read
    anyway when it carries JSON, because the server reports `{"status":"error","error":"prompt_not_found"}`
    with a 504 and that body is the diagnosis -- discarding it in favour of "HTTP 504" throws away the
    only useful part.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, slow_timeout: float = SLOW_TIMEOUT) -> None:
        self._timeout = timeout
        self._slow = max(timeout, slow_timeout)

    def health(self, instance: RelayInstance) -> dict[str, Any]:
        return self._get(instance, "/health")

    def open(self, instance: RelayInstance, url: str) -> dict[str, Any]:
        return self._post(instance, "/open", {"url": url})

    def new_chat(self, instance: RelayInstance) -> dict[str, Any]:
        return self._post(instance, "/new_chat", {})

    def type(self, instance: RelayInstance, text: str) -> dict[str, Any]:
        return self._post(instance, "/type", {"text": text})

    def send(self, instance: RelayInstance) -> dict[str, Any]:
        return self._post(instance, "/send", {})

    def read_thread(self, instance: RelayInstance, last: int | None = None) -> ThreadView:
        query = f"?last={int(last)}" if last else ""
        return self.to_thread(self._get(instance, f"/read_thread{query}"))

    @staticmethod
    def to_thread(body: dict[str, Any]) -> ThreadView:
        """The server's `{"thread": [...], "turn_count": n}` as a ThreadView. A malformed row is dropped
        rather than failing the read: a half-rendered bubble must not lose the rest of the thread."""
        turns: list[RelayTurn] = []
        for row in (body.get("thread") or [])[:MAX_THREAD_TURNS]:
            if isinstance(row, dict) and row.get("role") in ("user", "assistant"):
                turns.append(RelayTurn(role=str(row["role"]), turn_id=str(row.get("turn_id") or ""),
                                       text=str(row.get("text") or "")))
        count = body.get("turn_count")
        return ThreadView(turns=turns, turn_count=int(count) if isinstance(count, int) else len(turns))

    def _patience(self, path: str) -> float:
        return self._slow if path.split("?")[0] in SLOW else self._timeout

    def _get(self, instance: RelayInstance, path: str) -> dict[str, Any]:
        return self._call(urllib.request.Request(instance.base_url.rstrip("/") + path, method="GET"),
                          instance, self._patience(path))

    def _post(self, instance: RelayInstance, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body).encode()
        request = urllib.request.Request(instance.base_url.rstrip("/") + path, data=payload, method="POST",
                                         headers={"Content-Type": "application/json"})
        return self._call(request, instance, self._patience(path))

    def _call(self, request: urllib.request.Request, instance: RelayInstance,
              timeout: float | None = None) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=timeout or self._timeout) as answer:
                return self._decode(answer.read())
        except urllib.error.HTTPError as exc:                 # the body is the diagnosis; keep it
            try:
                return self._decode(exc.read())
            except (OSError, ValueError):
                raise RelayUnreachable(f"{instance.label} answered HTTP {exc.code} with no detail") from None
        except (OSError, ValueError) as exc:
            raise RelayUnreachable(f"{instance.label} ({instance.base_url}) did not answer "
                                   f"({type(exc).__name__}). Is the browser server up, and is tailscale?") from None

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        body = json.loads(raw.decode("utf-8", errors="replace"))
        return body if isinstance(body, dict) else {"body": body}


class RetryingTransport:
    """Decorator: one retry on an unreachable call, because the first request after an idle period routinely
    loses while tailscale brings a path up -- the same lesson the ssh retry loop in HANDOFF.md records.

    Reads are retried. `type` and `send` are **not**, and that asymmetry is the whole reason this is a
    Decorator rather than a flag: retrying a send that in fact arrived would post the message twice into
    somebody's real conversation.
    """

    RETRIED = ("health", "open", "new_chat", "read_thread")

    def __init__(self, inner: IRelayTransport, clock: IRelayClock, pause: float = 1.5) -> None:
        self._inner, self._clock, self._pause = inner, clock, pause

    def health(self, instance: RelayInstance) -> dict[str, Any]:
        return self._once("health", lambda: self._inner.health(instance))

    def open(self, instance: RelayInstance, url: str) -> dict[str, Any]:
        return self._once("open", lambda: self._inner.open(instance, url))

    def new_chat(self, instance: RelayInstance) -> dict[str, Any]:
        return self._once("new_chat", lambda: self._inner.new_chat(instance))

    def type(self, instance: RelayInstance, text: str) -> dict[str, Any]:
        return self._inner.type(instance, text)

    def send(self, instance: RelayInstance) -> dict[str, Any]:
        return self._inner.send(instance)

    def read_thread(self, instance: RelayInstance, last: int | None = None) -> ThreadView:
        return self._once("read_thread", lambda: self._inner.read_thread(instance, last))

    def _once(self, what: str, call):
        try:
            return call()
        except RelayUnreachable:
            if what not in self.RETRIED:
                raise
            self._clock.sleep(self._pause)
            return call()


class ObservedTransport:
    """Decorator: tells an IRelayObserver about each call, so the lifecycle log shows the conversation with
    the browser server and not just its result. Changes nothing it passes through."""

    def __init__(self, inner: IRelayTransport, observer: IRelayObserver) -> None:
        self._inner, self._observer = inner, observer

    def health(self, instance: RelayInstance) -> dict[str, Any]:
        return self._note("health", instance, lambda: self._inner.health(instance))

    def open(self, instance: RelayInstance, url: str) -> dict[str, Any]:
        return self._note("open", instance, lambda: self._inner.open(instance, url))

    def new_chat(self, instance: RelayInstance) -> dict[str, Any]:
        return self._note("new_chat", instance, lambda: self._inner.new_chat(instance))

    def type(self, instance: RelayInstance, text: str) -> dict[str, Any]:
        # The text itself is never logged: it is somebody's message, and it is the one argument that could
        # carry something private. Its length is enough to debug a typing failure.
        return self._note(f"type {len(text)} chars", instance, lambda: self._inner.type(instance, text))

    def send(self, instance: RelayInstance) -> dict[str, Any]:
        return self._note("send", instance, lambda: self._inner.send(instance))

    def read_thread(self, instance: RelayInstance, last: int | None = None) -> ThreadView:
        return self._note("read_thread", instance, lambda: self._inner.read_thread(instance, last))

    def _note(self, what: str, instance: RelayInstance, call):
        self._observer.waiting(0, 0.0, f"{instance.label}: {what}")
        return call()

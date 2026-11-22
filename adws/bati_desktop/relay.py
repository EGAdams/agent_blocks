"""The WSL half of BATI's DevTools relay, and the two cdp_browser adapters that run over it.

`WindowsPythonRelay` starts `relay_win.py` under BATI's Windows Python for the length of one run and exchanges one
JSON line per request with it. `RelayTargetDirectory` (ITargetDirectory) and `RelayCdpConnector` (ICdpConnector)
let the existing `cdp_browser.CdpWorkflowBrowser` drive the tab unchanged, as if WSL could reach the port itself.

The script travels base64-encoded inside `-c`, so WSL interop's argument quoting can never alter it, and a stale
copy on Windows' disk can never be what runs.
"""
from __future__ import annotations

import base64
import json
import queue
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.channel import WebSocketCdpChannel
from cdp_browser.interfaces import CdpTarget, ICdpChannel
from cdp_browser.targets import NOT_A_PAGE, pinned

from .interfaces import IRelay

SCRIPT = Path(__file__).with_name("relay_win.py")


class RelayError(BrowserWorkflowError):
    """The relay refused, failed, or did not answer in time."""


class WindowsPythonRelay:
    """IRelay over a child Windows python.exe. Started on first use, closed by close()."""

    def __init__(self, python_exe: str, port: int, call_seconds: float, env: dict[str, str] | None = None) -> None:
        self._exe, self._port, self._call_seconds, self._env = python_exe, int(port), call_seconds, env
        self._proc: subprocess.Popen[bytes] | None = None
        self._answers: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._next = 0

    def _start(self) -> subprocess.Popen[bytes]:
        if self._proc is None:
            code = base64.b64encode(SCRIPT.read_bytes()).decode()
            boot = f"import base64;exec(compile(base64.b64decode('{code}'),'relay_win.py','exec'))"
            try:
                self._proc = subprocess.Popen([self._exe, "-I", "-u", "-c", boot, str(self._port)],
                                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              cwd="/mnt/c", env=self._env)
            except OSError as exc:
                raise RelayError(f"could not start Windows Python at {self._exe} ({exc})") from exc
            threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()
        return self._proc

    def _read(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                self._answers.put(json.loads(line.decode("utf-8")))
            except ValueError:
                continue
        self._answers.put(None)                                       # the relay exited

    def request(self, op: str, timeout: float | None = None, **fields: Any) -> dict[str, Any]:
        proc = self._start()
        self._next += 1
        wanted = self._next
        assert proc.stdin is not None
        try:
            proc.stdin.write((json.dumps({"id": wanted, "op": op, **fields}) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except OSError as exc:
            raise RelayError(f"the Windows relay is gone ({exc}){self._stderr()}") from exc
        wait = timeout if timeout is not None else self._call_seconds
        while True:
            try:
                answer = self._answers.get(timeout=wait)
            except queue.Empty:
                raise RelayError(f"the Windows relay did not answer '{op}' within {round(wait)}s") from None
            if answer is None:
                raise RelayError(f"the Windows relay exited during '{op}'{self._stderr()}")
            if answer.get("id") != wanted:
                continue                                              # a late answer to a request that timed out
            if not answer.get("ok"):
                raise RelayError(f"relay refused '{op}': {answer.get('error', '?')}")
            return answer

    def _stderr(self) -> str:
        if self._proc and self._proc.poll() is not None and self._proc.stderr:
            said = self._proc.stderr.read().decode("utf-8", "replace").strip()
            return f": {said[-300:]}" if said else ""
        return ""

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self.request("quit", timeout=2)
        except RelayError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None


class RelayTargetDirectory:
    """ITargetDirectory over the relay. No browser socket and no close: neither is needed, and neither is allowed."""

    def __init__(self, relay: IRelay) -> None:
        self._relay = relay

    def _get(self, path: str, method: str = "GET") -> str:
        return str(self._relay.request("http", method=method, path=path).get("body", ""))

    def pages(self, endpoint: DebugEndpoint) -> list[CdpTarget]:
        found = [CdpTarget.model_validate(t) for t in json.loads(self._get("/json/list"))]
        return [t for t in found if t.type == "page" and not t.url.startswith(NOT_A_PAGE)]

    def version(self) -> dict[str, Any]:
        return json.loads(self._get("/json/version"))

    def browser_socket(self, endpoint: DebugEndpoint) -> str:
        raise BrowserWorkflowError("the browser-level socket is not available on BATI (it could quit Chrome)")

    def open_page(self, endpoint: DebugEndpoint, url: str) -> CdpTarget:
        return CdpTarget.model_validate(json.loads(self._get(f"/json/new?{quote(url, safe='')}", method="PUT")))

    def activate(self, endpoint: DebugEndpoint, target_id: str) -> None:
        self._get(f"/json/activate/{target_id}")

    def close_page(self, endpoint: DebugEndpoint, target_id: str) -> None:
        raise BrowserWorkflowError("closing a tab is not an operation on BATI")


class RelayWebSocket:
    """The four calls WebSocketCdpChannel makes on a socket, answered by the relay."""

    def __init__(self, relay: IRelay, path: str, call_seconds: float) -> None:
        self._relay, self._call_seconds = relay, call_seconds
        self._sock = int(relay.request("ws_open", path=path)["sock"])
        self._timeout = 10.0

    def settimeout(self, seconds: float) -> None:
        self._timeout = max(0.1, float(seconds))

    def send_text(self, text: str) -> None:
        self._relay.request("ws_send", sock=self._sock, text=text)

    def recv_text(self) -> str:
        wait = min(self._timeout, self._call_seconds)
        answer = self._relay.request("ws_recv", wait + 5, sock=self._sock, wait=wait)
        if answer.get("timeout"):
            raise socket.timeout()
        return str(answer["text"])

    def close(self) -> None:
        try:
            self._relay.request("ws_close", sock=self._sock, timeout=3)
        except RelayError:
            pass


class RelayCdpConnector:
    """ICdpConnector: a page websocket, opened by the relay on Windows' loopback."""

    def __init__(self, relay: IRelay, endpoint: DebugEndpoint, call_seconds: float) -> None:
        self._relay, self._endpoint, self._call_seconds = relay, endpoint, call_seconds

    def connect(self, websocket_url: str) -> ICdpChannel:
        if urlparse(websocket_url).hostname not in ("127.0.0.1", "localhost"):
            raise BrowserWorkflowError(f"not a loopback DevTools socket: {websocket_url}")
        url = urlparse(pinned(websocket_url, self._endpoint))
        if url.scheme != "ws" or f"{url.hostname}:{url.port}" != self._endpoint.address:
            raise BrowserWorkflowError(f"not this machine's debug Chrome: {websocket_url}")
        return WebSocketCdpChannel(RelayWebSocket(self._relay, url.path, self._call_seconds))  # type: ignore[arg-type]

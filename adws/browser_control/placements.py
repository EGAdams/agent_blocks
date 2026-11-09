"""Remembering where the debug Chrome is, so Windows is asked once per Chrome rather than once per operation.

Why. On the live box (2026-11-09) WSL interop stalls in bursts -- `UtilAcceptVsock: accept4 failed 110`, ten
seconds a call, several calls in a row -- so asking Windows "which session is Chrome in?" before every page read
failed whole verifications at random, though nothing about Chrome had changed. A process's Windows session is
fixed for its whole life, so the answer for one Chrome browser process stays true until that process ends.

Two parts, each against its own interface:

- `CdpBrowserProcess` (IBrowserProcess): the browser process id of the Chrome on a debug port, asked of Chrome
  itself over DevTools (`SystemInfo.getProcessInfo`) -- no Windows call, so no interop.
- `RememberedPlacement` (IWindowSessions): a caching Proxy over the real probe. It answers from memory only when
  Chrome reports the same browser pid that Windows placed earlier; any other pid, or no pid, asks Windows, and
  only an answer that found Chrome (with that same pid) is remembered. A Chrome that restarted is a new pid, so
  it is asked about afresh -- a hidden session-0 Chrome can never inherit a visible one's answer.
"""
from __future__ import annotations

import json
from pathlib import Path

from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.channel import WebSocketCdpConnector
from cdp_browser.targets import HttpTargetDirectory, LoopbackOnly

from .interfaces import IBrowserProcess, IWindowSessions
from .models import BrowserKind, BrowserPlacement


class CdpBrowserProcess:
    """IBrowserProcess over the browser-level DevTools socket. 0 when Chrome does not answer. Never raises."""

    def pid(self, kind: BrowserKind) -> int:
        try:
            socket_url = HttpTargetDirectory(LoopbackOnly(), timeout_seconds=3.0).browser_socket(DebugEndpoint(port=kind.port))
            channel = WebSocketCdpConnector(timeout_seconds=3.0).connect(socket_url)
        except (BrowserWorkflowError, OSError, ValueError, KeyError):
            return 0
        try:
            rows = channel.call("SystemInfo.getProcessInfo", timeout_seconds=3.0).get("processInfo") or []
            return next((int(r.get("id") or 0) for r in rows if r.get("type") == "browser"), 0)
        except (BrowserWorkflowError, OSError, ValueError, TypeError):
            return 0
        finally:
            try:
                channel.close()
            except OSError:
                pass


class RememberedPlacement:
    """IWindowSessions: the inner probe's answer, remembered per debug port for as long as the same Chrome
    browser process owns it. The memory is one small JSON file; an unreadable file is simply forgotten."""

    def __init__(self, inner: IWindowSessions, process: IBrowserProcess, memory: Path) -> None:
        self._inner, self._process, self._memory = inner, process, memory

    def placement(self, kind: BrowserKind) -> BrowserPlacement:
        pid = self._process.pid(kind)
        remembered = self._load().get(str(kind.port))
        if pid and remembered:
            try:
                known = BrowserPlacement.model_validate(remembered)
            except ValueError:
                known = None
            if known and known.known and known.found and known.pid == pid:
                return known
        asked = self._inner.placement(kind)
        if pid and asked.known and asked.found and asked.pid == pid:
            self._save(kind, asked)
        return asked

    def _load(self) -> dict:
        try:
            data = json.loads(self._memory.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, kind: BrowserKind, placement: BrowserPlacement) -> None:
        data = self._load()
        data[str(kind.port)] = placement.model_dump()
        try:
            self._memory.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._memory.with_suffix(".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(self._memory)
        except OSError:
            pass                                             # remembering is a convenience; asking again is correct

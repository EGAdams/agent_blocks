"""BATI desktop Chrome operations: status, open, show_view, read, screenshot, and plan_for_today which composes them.

Each one is narrow by construction:
* the machine guard runs first and refuses anything but BATI -- WSL's hostname AND Windows' COMPUTERNAME;
* `open` takes only a URL on one of the policy's origins (the dashboard);
* `show_view` presses only a dashboard navigation tab named in the policy (`button.tab[data-nav=main]`);
* `read` returns the URL without query or fragment, the title, which view is showing, its heading and status line
  -- never form values, cookies or storage;
* there is no type, fill, close or quit, and the relay itself refuses /json/close and the browser-level socket.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from browser_control.models import BrowserKind
from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.interfaces import CdpTarget, ICdpConnector

from .interfaces import IHostName, IRelay, ISessions, IStarter
from .models import BatiPolicy
from .relay import RelayTargetDirectory


class Refused(BrowserWorkflowError):
    """An operation stopped on purpose. `cause` is the short code reported and audited."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


SHOW_VIEW_JS = r"""(() => {
  const view = %s;
  const button = document.querySelector('button.tab[data-nav="main"][data-target="' + view + '"]');
  if (!button) return {pressed: false, why: 'no dashboard tab for ' + view};
  button.click();
  return {pressed: true};
})()"""

READ_JS = r"""(() => {
  const view = %s;
  const el = view ? document.getElementById(view) : document.querySelector('section.view.active');
  const text = (sel) => { const n = el && el.querySelector(sel); return n ? n.textContent.trim() : ''; };
  return {
    href: location.href, title: document.title, visibility: document.visibilityState, focused: document.hasFocus(),
    active_view: (document.querySelector('section.view.active') || {}).id || '',
    view_active: !!(el && el.classList.contains('active')),
    heading: text('h2'), status: text('[role=status]'),
    tasks: el ? el.querySelectorAll('.plan-for-today-body input[type=checkbox]').length : 0,
  };
})()"""


def bare(url: str) -> str:
    """A URL as reported and audited: no query, no fragment, no credentials."""
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname or ''}{':' + str(u.port) if u.port else ''}{u.path}"


def long_date(day: date) -> str:
    """The en-US form the dashboard heading uses: 'November 21, 2026'."""
    return f"{day.strftime('%B')} {day.day}, {day.year}"


class BatiChrome:
    """The operations, over injected parts. Built only by kit.build()."""

    def __init__(self, policy: BatiPolicy, kind: BrowserKind, relay: IRelay, directory: RelayTargetDirectory,
                 connector: ICdpConnector, starter: IStarter, sessions: ISessions, hostname: IHostName,
                 screens: Path, today: date | None = None, sleep: Any = time.sleep) -> None:
        self._p, self._kind, self._relay, self._dir = policy, kind, relay, directory
        self._connector, self._starter, self._sessions, self._hostname = connector, starter, sessions, hostname
        self._screens, self._today, self._sleep = screens, today, sleep
        self._endpoint = DebugEndpoint(port=policy.port)

    # -- guards -------------------------------------------------------------------------------------------------

    def guard_machine(self) -> dict[str, Any]:
        here = self._hostname()
        if here.upper() != self._p.hostname.upper():
            raise Refused("wrong_machine", f"this is {here}, not {self._p.machine} ({self._p.hostname}); "
                                           "BATI's desktop is driven only from BATI, never rerouted")
        hello = self._relay.request("hello")
        if str(hello.get("computer", "")).upper() != self._p.hostname.upper():
            raise Refused("wrong_machine", f"Windows says it is {hello.get('computer')!r}, not {self._p.hostname}")
        return {"wsl_host": here, "windows_computer": hello.get("computer"), "windows_user": hello.get("user")}

    def _allowed_url(self, url: str) -> str:
        u = urlparse(url)
        origin = f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}"
        if u.username or u.password or origin not in self._p.origins:
            raise Refused("url_not_allowed", f"{bare(url)} is not on an allowed origin ({', '.join(self._p.origins)})")
        return url

    def _visible(self) -> dict[str, Any]:
        where = self._sessions.placement(self._kind)
        if not where.known:
            raise Refused("visibility_unknown", where.reason or "Windows did not say where Chrome is")
        if not where.desktops:
            raise Refused("no_desktop", "nobody is signed in to BATI's Windows desktop; stopping")
        if not where.found or where.session == 0 or where.session not in where.desktops:
            raise Refused("hidden_session", f"the debug Chrome is in session {where.session}, not on a desktop "
                                            f"({where.desktops}); nobody could see it")
        return {"pid": where.pid, "session": where.session, "desktops": where.desktops}

    def _running(self) -> dict[str, Any] | None:
        try:
            return self._dir.version()
        except BrowserWorkflowError:
            return None

    def _dashboard_tab(self) -> CdpTarget:
        for page in self._dir.pages(self._endpoint):                # /json/list is most-recent first
            u = urlparse(page.url)
            if f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}" in self._p.origins:
                return page
        raise Refused("no_dashboard_tab", "the debug Chrome has no dashboard tab open; run `open` first")

    def _evaluate(self, target: CdpTarget, expression: str) -> Any:
        channel = self._connector.connect(target.webSocketDebuggerUrl)
        try:
            result = channel.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        finally:
            channel.close()
        if "exceptionDetails" in result:
            raise BrowserWorkflowError(f"the page script failed: {result['exceptionDetails'].get('text', '?')}")
        return result.get("result", {}).get("value")

    # -- operations ---------------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        data = self.guard_machine()
        version = self._running()
        data["debug_chrome"] = bool(version)
        if version:
            data["browser"] = version.get("Browser", "")
            data["placement"] = self._visible()
            data["tabs"] = [{"url": bare(p.url), "title": p.title} for p in self._dir.pages(self._endpoint)]
        return data

    def open(self, url: str) -> dict[str, Any]:
        self._allowed_url(url)
        data = self.guard_machine()
        if self._running() is None:
            ok, why = self._starter.start(self._kind, url)
            if not ok:
                cause = "no_desktop" if "signed in" in why else "launch"
                raise Refused(cause, f"Chrome was not started on BATI's desktop: {why}")
            deadline = time.monotonic() + self._p.launch_wait_seconds
            while self._running() is None:
                if time.monotonic() > deadline:
                    raise Refused("control_port", f"Chrome was started but 127.0.0.1:{self._p.port} never answered")
                self._sleep(0.5)
            data["launched"] = True
        else:
            data["launched"] = False
            try:
                self._dashboard_tab()                               # one dashboard tab is reused, not multiplied
                data["reused_tab"] = True
            except Refused:
                page = self._dir.open_page(self._endpoint, url)
                self._dir.activate(self._endpoint, page.id)
                data["reused_tab"] = False
        data["placement"] = self._visible()
        tab = self._dashboard_tab()
        self._dir.activate(self._endpoint, tab.id)
        data["tab"] = {"id": tab.id, "url": bare(tab.url)}
        return data

    def show_view(self, view: str) -> dict[str, Any]:
        if view not in self._p.views:
            raise Refused("view_not_allowed", f"'{view}' is not a view this may open ({', '.join(self._p.views)})")
        data = self.guard_machine()
        tab = self._dashboard_tab()
        self._dir.activate(self._endpoint, tab.id)
        deadline = time.monotonic() + self._p.view_wait_seconds
        presses, seen = 0, {}
        # A freshly launched page has the button before its module scripts have bound the click listener, so a
        # press can "succeed" and do nothing. Press, give the view a moment, and press again until it is active.
        # Pressing a nav tab only switches the view (and re-reads the note); it never writes.
        while True:
            pressed = self._evaluate(tab, SHOW_VIEW_JS % json.dumps(view)) or {}
            if pressed.get("pressed"):
                presses += 1
                settle = time.monotonic() + min(3.0, self._p.view_wait_seconds / 4)
                while time.monotonic() < min(settle, deadline):
                    seen = self._evaluate(tab, READ_JS % json.dumps(view)) or {}
                    if seen.get("view_active") and seen.get("status") not in ("", "Loading…"):
                        data.update(view=view, status=seen.get("status"), presses=presses)
                        return data
                    self._sleep(0.5)
            if time.monotonic() > deadline:
                if not presses:
                    raise Refused("no_view", str(pressed.get("why", f"could not press the {view} tab")))
                raise Refused("view_not_shown", f"pressed the {view} tab {presses}x but it did not become the "
                                                f"shown view (active: {seen.get('active_view')!r}, "
                                                f"status {seen.get('status')!r})")
            self._sleep(0.5)

    def read(self, view: str = "") -> dict[str, Any]:
        data = self.guard_machine()
        tab = self._dashboard_tab()
        seen = self._evaluate(tab, READ_JS % json.dumps(view)) or {}
        seen["href"] = bare(str(seen.get("href", "")))
        data["page"] = seen
        data["placement"] = self._visible()
        return data

    def screenshot(self, name: str) -> dict[str, Any]:
        data = self.guard_machine()
        tab = self._dashboard_tab()
        channel = self._connector.connect(tab.webSocketDebuggerUrl)
        try:
            shot = channel.call("Page.captureScreenshot", {"format": "png"}).get("data")
        finally:
            channel.close()
        if not shot:
            raise Refused("no_image", "Chrome gave no screenshot of the tab")
        import base64
        self._screens.mkdir(parents=True, exist_ok=True)
        path = self._screens / f"{name}.png"
        path.write_bytes(base64.b64decode(shot))
        data["screenshot"] = str(path)
        data["bytes"] = path.stat().st_size
        return data

    def plan_for_today(self, url: str, name: str) -> tuple[bool, dict[str, Any]]:
        """open -> show_view plan-for-today -> read -> screenshot. Verified only if the page, read back, shows the
        Plan for Today view with today's date in its heading on an allowed URL, in a visible tab."""
        today = self._today or date.today()
        steps: dict[str, Any] = {"open": self.open(url), "show_view": self.show_view("plan-for-today")}
        steps["read"] = self.read("plan-for-today")
        steps["screenshot"] = self.screenshot(name)
        page = steps["read"]["page"]
        expected = long_date(today)
        checks = {
            "view_active": bool(page.get("view_active")) and page.get("active_view") == "plan-for-today",
            "date_in_heading": expected in str(page.get("heading", "")),
            "allowed_url": any(page.get("href", "").startswith(o) for o in self._p.origins),
            "visible": page.get("visibility") == "visible",
        }
        steps["expected_date"] = expected
        steps["checks"] = checks
        return all(checks.values()), steps

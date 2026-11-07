"""The ten Browser Control operations. Each is one `Operation` (browser_workflows' base class, so
the ADW harness runs it as a lifecycle phase and the object trace records it) over a ControlPlane.

    browser.open  browser.navigate  browser.refresh  browser.tabs  browser.select_tab
    browser.click browser.read      browser.screenshot browser.status browser.close

Every result is verified, not assumed: `open` waits until the DevTools port answers and lists a tab,
`navigate` reports the URL the tab ended on, `close` re-lists the tabs. Nothing here reads cookies,
storage or password fields; URLs are reported without their query or fragment (they carry tokens).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import RecordedAction
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.operations.common import Operation, OperationOutcome, locators_from, ok, refused
from browser_workflows.playback.locating import RankedElementResolver, default_strategy_registry

from .models import BrowserKind, TabView
from .plane import ControlPlane, checked_url

MAX_READ_CHARS = 20000


def safe_url(url: str) -> str:
    """scheme://host/path -- the query and fragment can hold tokens, so they are never reported."""
    parts = urlparse(url or "")
    if not parts.scheme or not parts.netloc:
        return (url or "")[:80]
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + ("?…" if parts.query or parts.fragment else "")


class BrowserOperation(Operation):
    """Shared plumbing: pick the browser, require it to be up, find the chosen tab."""
    performer = "browser_control"
    destructive = False

    def __init__(self, plane: ControlPlane) -> None:
        self._plane = plane

    def _kind(self, args: dict[str, Any]) -> BrowserKind:
        return self._plane.catalog.kind(args.get("browser"))

    def _up(self, kind: BrowserKind) -> str:
        up, detail = self._plane.endpoint.reachable(kind)
        if not up:
            raise BrowserWorkflowError(
                f"{kind.name} is not running with its control port ({detail}). Say 'open a browser' first.")
        return detail

    def _tabs(self, kind: BrowserKind) -> list[TabView]:
        return [TabView(number=i, id=t.id, url=safe_url(t.url), title=(t.title or "")[:120])
                for i, t in enumerate(self._plane.endpoint.pages(kind), start=1)]

    def _tab_id(self, kind: BrowserKind, args: dict[str, Any]) -> str | None:
        """`tab` is a list number (1, 2...) or a tab id from browser.tabs. None means the active tab."""
        wanted = str(args.get("tab") or "").strip()
        if not wanted:
            return None
        tabs = self._tabs(kind)
        if wanted.isdigit() and 1 <= int(wanted) <= len(tabs):
            return tabs[int(wanted) - 1].id
        if any(t.id == wanted for t in tabs):
            return wanted
        raise BrowserWorkflowError(f"no open tab {wanted!r} in {kind.name}; there are {len(tabs)}")

    def _page(self, kind: BrowserKind, args: dict[str, Any]):
        self._up(kind)
        return self._plane.attacher.attach(kind, self._tab_id(kind, args))


class Launch(BrowserOperation):
    """Phase 1 of browser.open: make sure Chrome is running where the person can see it.

    Already running and visible (or Windows can't say): leave it alone. Running but hidden in session 0
    (the interop launch of earlier versions): close that one -- nobody can see or use it -- and start a
    visible one. Not running: start it through the desktop starter and wait for its control port."""
    name = "browser_launch"
    description = "browser.open, phase 1: start Chrome on the signed-in person's desktop (replacing a hidden one)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        url = checked_url(args["url"]) if args.get("url") else "about:blank"
        up, detail = self._plane.endpoint.reachable(kind)
        replaced = False
        linked, why = self._plane.link.ensure()                 # PowerShell and chrome.exe both need it
        if up and linked and self._plane.sessions is not None:
            where = self._plane.sessions.placement(kind)
            if where.known and where.found and not where.visible:
                self._plane.endpoint.shutdown(kind)
                if not self._wait(kind, want_up=False, seconds=10.0):
                    return refused(f"{kind.name} is running in hidden Windows session {where.session}, and it would not close "
                                   "so a visible one could start.", browser=kind.name, verified=False, cause="hidden_session")
                up, replaced = False, True
        if up:
            return ok(f"{kind.name} is already running ({detail}).", browser=kind.name, version=detail,
                      started=False, replaced_hidden=False)
        if not linked:
            return refused(why, browser=kind.name, started=False, verified=False, cause="windows_interop")
        launched, reason = self._plane.starter.start(kind, url)
        if not launched:
            return refused(f"I could not start {kind.name}: {reason}", browser=kind.name, verified=False, cause="launch")
        if not self._wait(kind, want_up=True, seconds=self._plane.ready_seconds):
            _, detail = self._plane.endpoint.reachable(kind)
            return refused(f"I started {kind.name} but could not control it: "
                           f"{self._plane.link.explain_unreachable(kind.port)} ({detail})",
                           browser=kind.name, started=True, verified=False, cause="control_port")
        _, detail = self._plane.endpoint.reachable(kind)
        return ok(f"{kind.name} started ({detail})" + (", replacing a hidden one." if replaced else "."),
                  browser=kind.name, version=detail, started=True, replaced_hidden=replaced)

    def _wait(self, kind: BrowserKind, want_up: bool, seconds: float) -> bool:
        deadline = self._plane.clock.monotonic() + seconds
        while True:
            if self._plane.endpoint.reachable(kind)[0] == want_up:
                return True
            if self._plane.clock.monotonic() >= deadline:
                return False
            self._plane.clock.sleep(0.5)


class Visibility(BrowserOperation):
    """Phase 2 of browser.open: Windows confirms the Chrome on the control port is on a desktop a person can see.
    "The port answers" only proves it runs; a Chrome in session 0 answers too and is never seen."""
    name = "browser_visibility"
    description = "browser.open, phase 2: confirm with Windows that Chrome is on a visible desktop, not hidden session 0"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if self._plane.sessions is None:
            return refused(f"I can't check where {kind.name} opened, so I won't say you can see it.",
                           browser=kind.name, verified=False, cause="visibility_unknown")
        linked, why = self._plane.link.ensure()
        if not linked:
            return refused(f"{kind.name} is running, but I couldn't ask Windows whether it is on your screen: {why}",
                           browser=kind.name, verified=False, cause="visibility_unknown")
        where = self._plane.sessions.placement(kind)
        facts = dict(browser=kind.name, pid=where.pid, session=where.session, desktops=where.desktops)
        if not where.known:
            return refused(f"{kind.name} is running, but I couldn't confirm it is on your screen: {where.reason}",
                           verified=False, cause="visibility_unknown", **facts)
        if not where.found:
            return refused(f"Windows shows no {kind.name} owning control port {kind.port}.",
                           verified=False, cause="visibility_unknown", **facts)
        if not where.visible:
            hidden = "the hidden services session (0)" if where.session == 0 else f"session {where.session}, which has no desktop"
            return refused(f"{kind.name} is running but not on your screen: Windows has it in {hidden}; "
                           f"desktops are in session(s) {where.desktops or 'none'}.", verified=False, cause="hidden_session", **facts)
        return ok(f"{kind.name} is on the desktop in Windows session {where.session}.", visible=True, **facts)


class Open(BrowserOperation):
    """Phase 3 of browser.open: add the asked-for tab to a Chrome that was already running, and verify the tabs.
    The catalog runs it after Launch and Visibility (an OperationSequence), whose data it reads."""
    name = "browser_open"
    description = "browser.open: start Chrome on the Windows desktop with a control port (or add a tab)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        detail = self._up(kind)
        started = bool(args.get("started"))
        if args.get("url") and not started:                  # already running: add a tab, touch nothing else
            self._plane.endpoint.open_page(kind, checked_url(args["url"]))
        tabs = self._tabs(kind)
        if not tabs:
            return refused(f"{kind.name} answered but shows no open page.", browser=kind.name, verified=False)
        what = "started" if started else "was already running"
        if args.get("replaced_hidden"):
            what = "started (a hidden copy was closed first)"
        where = f" in Windows session {args['session']}" if args.get("visible") else ""
        return ok(f"{kind.name} {what} on your desktop{where} ({detail}); {len(tabs)} tab(s) open.",
                  browser=kind.name, version=detail, started=started, verified=True, tabs=[t.model_dump() for t in tabs])


class Navigate(BrowserOperation):
    name = "browser_navigate"
    description = "browser.navigate: go to an http(s) URL in the chosen (or active) tab"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind, url = self._kind(args), checked_url(args.get("url"))
        page = self._page(kind, args)
        try:
            page.navigate(url)
            now, title = page.current_url, page.title
        finally:
            page.release()
        landed = urlparse(now).netloc == urlparse(url).netloc
        line = f"Now at {safe_url(now)} ({title[:80]!r})." if landed else \
               f"Asked for {safe_url(url)} but the tab is at {safe_url(now)} ({title[:80]!r}) -- it was redirected."
        return ok(line, browser=kind.name, url=safe_url(now), title=title[:120], redirected=not landed, verified=True)


class Refresh(BrowserOperation):
    name = "browser_refresh"
    description = "browser.refresh: reload the chosen (or active) tab"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        try:
            before = safe_url(page.current_url)
            page.reload()
            now, title = safe_url(page.current_url), page.title
        finally:
            page.release()
        return ok(f"Reloaded {now} ({title[:80]!r}).", browser=kind.name, url=now, title=title[:120],
                  was=before, verified=True)


class Tabs(BrowserOperation):
    name = "browser_tabs"
    description = "browser.tabs: list the open tabs (number, id, URL without query, title)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        self._up(kind)
        tabs = self._tabs(kind)
        lines = "\n".join(f"{t.number}. {t.title or '(untitled)'} -- {t.url}" for t in tabs)
        return ok(f"{len(tabs)} tab(s) in {kind.name}:\n{lines}" if tabs else f"{kind.name} has no open page.",
                  browser=kind.name, tabs=[t.model_dump() for t in tabs], verified=True)


class SelectTab(BrowserOperation):
    name = "browser_select_tab"
    description = "browser.select_tab: bring one tab (by number or id) to the front"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if not args.get("tab"):
            return refused("Say which tab: its number from browser.tabs, or its id.")
        self._up(kind)
        wanted = self._tab_id(kind, args)
        self._plane.endpoint.activate(kind, wanted or "")
        page = self._plane.attacher.attach(kind, wanted)
        try:
            url, title = safe_url(page.current_url), page.title
            self._plane.clock.sleep(0.3)
            in_front = page.is_foreground()                  # the page's own visibilityState, not the list order
        finally:
            page.release()
        if not in_front:
            return refused("I asked for that tab, but the browser still lists another one in front.",
                           browser=kind.name, tab=wanted, verified=False)
        return ok(f"Tab {args['tab']} is now in front: {title[:80]!r} at {url}.", browser=kind.name, tab=wanted,
                  url=url, title=title[:120], verified=True)


class Click(BrowserOperation):
    name = "browser_click"
    description = "browser.click: press one element, named by text, label or css. Consequential ones need confirm=true"
    destructive = True

    def __init__(self, plane: ControlPlane) -> None:
        super().__init__(plane)
        self._resolver = RankedElementResolver(default_strategy_registry())
        self._classifier = KeywordConsequenceClassifier()

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        candidates = locators_from(args)
        hint = " ".join(str(args.get(k) or "") for k in ("text", "label"))
        why = self._classifier.reason(RecordedAction(action_type="click", metadata={"text": hint}))
        if why and not args.get("confirm"):
            return refused(f"I did not click: {why}. Ask EG, then repeat with confirm=true.", browser=kind.name,
                           needs_confirmation=True, verified=False)
        page = self._page(kind, args)
        try:
            element, resolution = self._resolver.resolve(page, candidates)
            if element is None:
                tried = ", ".join(f"{a.label}: {a.outcome}" for a in resolution.attempts)
                return refused(f"I could not find one clickable element for that ({tried}).",
                               browser=kind.name, verified=False)
            if element.tag_name == "input" and (element.get_attribute("type") or "").lower() == "password":
                return refused("That is a password field; I do not touch those.", browser=kind.name, verified=False)
            label = (element.text or "")[:60]
            before = page.current_url
            element.click()
            after = page.current_url
        finally:
            page.release()
        moved = safe_url(after) if after != before else ""
        return ok(f"Clicked {label!r}" + (f"; the page moved to {moved}." if moved else "."),
                  browser=kind.name, clicked=label, moved_to=moved, winner=resolution.winner, verified=True)


class Read(BrowserOperation):
    name = "browser_read"
    description = "browser.read: the visible text of the page (never form values, cookies or storage)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        try:
            text, url, title = page.body_text(), safe_url(page.current_url), page.title
        finally:
            page.release()
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        clipped = len(text) > MAX_READ_CHARS
        return ok(f"{title[:80]!r} at {url}: {len(text)} characters of visible text.", browser=kind.name, url=url,
                  title=title[:120], text=text[:MAX_READ_CHARS], truncated=clipped, verified=True)


class Screenshot(BrowserOperation):
    name = "browser_screenshot"
    description = "browser.screenshot: save a PNG of the visible page and say where"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        page = self._page(kind, args)
        self._plane.shots.mkdir(parents=True, exist_ok=True)
        path = self._plane.shots / f"{kind.name}_{int(self._plane.clock.monotonic() * 1000)}.png"
        try:
            saved = page.capture_png(str(path))
            url = safe_url(page.current_url)
        finally:
            page.release()
        if not saved or not path.exists() or path.stat().st_size == 0:
            return refused("The browser returned no image.", browser=kind.name, verified=False)
        return ok(f"Saved a {path.stat().st_size}-byte screenshot of {url}.", browser=kind.name, path=str(path),
                  bytes=path.stat().st_size, url=url, verified=True)


class Status(BrowserOperation):
    name = "browser_status"
    description = "browser.status: is Chrome up, which version, how many tabs"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        names = [args.get("browser") or "chrome"]
        report, lines = [], []
        for name in names:
            kind = self._plane.catalog.kind(name)
            up, detail = self._plane.endpoint.reachable(kind)
            tabs = len(self._plane.endpoint.pages(kind)) if up else 0
            report.append({"browser": kind.name, "running": up, "version": detail if up else "", "tabs": tabs,
                           "port": kind.port, "reason": "" if up else detail})
            lines.append(f"{kind.name}: " + (f"running ({detail}), {tabs} tab(s)" if up else f"not running ({detail})"))
        return ok("; ".join(lines), browsers=report, verified=True)


class Close(BrowserOperation):
    name = "browser_close"
    description = "browser.close: close ONE explicitly chosen tab; it never quits the browser or touches other windows"
    destructive = True

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if not args.get("tab"):
            return refused("Say exactly which tab to close: its number or id from browser.tabs. I never close by guessing.")
        self._up(kind)
        target = self._tab_id(kind, args)
        before = self._tabs(kind)
        if len(before) == 1:
            return refused("That is the only open tab; closing it would quit the browser, so I left it.",
                           browser=kind.name, verified=False)
        self._plane.endpoint.close_page(kind, target or "")
        self._plane.clock.sleep(0.3)
        after = self._tabs(kind)
        gone = all(t.id != target for t in after)
        if not gone:
            return refused("I asked the browser to close that tab, but it is still listed.", browser=kind.name, verified=False)
        return ok(f"Closed tab {args['tab']}; {len(after)} tab(s) remain.", browser=kind.name, closed=target,
                  remaining=len(after), verified=True)

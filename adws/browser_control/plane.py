"""ControlPlane: the injected parts every operation shares (Facade over the interfaces),
and the production implementations of them (reuse of browser_session / cdp_browser)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from browser_session.chrome_command import ChromeCommandBuilder
from browser_session.chrome_launch import DebugChromeLauncher
from browser_session.process_spawner import DetachedProcessSpawner
from browser_session.profile_dir import WindowsLocalAppDataProfile
from browser_session.models import DebugEndpoint
from browser_workflows.errors import BrowserWorkflowError
from cdp_browser.channel import WebSocketCdpConnector
from cdp_browser.choosers import FirstPageChooser, PreferredTargetChooser
from cdp_browser.browser import CdpWorkflowBrowser
from cdp_browser.interfaces import CdpTarget
from cdp_browser.targets import HttpTargetDirectory, LoopbackOnly
from browser_workflows.playback.download_directory import NativeDownloadDirectory
from browser_workflows.playback.downloads import FileDownloadVerifier, FolderDownloadWatcher

from .interfaces import (IBrowserCatalog, IBrowserEndpoint, IBrowserStarter, IClock, IDownloadFolder,
                         IDownloadVerifier, IDownloadWatcher, IPageAttacher, IPageSession, IWindowSessions,
                         IWindowsLink)
from .link import WslInteropLink
from .models import ALIASES, BROWSERS, BrowserKind
from .windows import InteractiveTaskStarter, WindowsSessionProbe

ALLOWED_SCHEMES = ("http", "https")


class StaticBrowserCatalog:
    """IBrowserCatalog over the BROWSERS table."""

    def kind(self, name: str | None) -> BrowserKind:
        key = ALIASES.get((name or "chrome").strip().lower(), (name or "chrome").strip().lower())
        if key not in BROWSERS:
            raise BrowserWorkflowError(f"Chrome is the only authorized browser; {name!r} is not available. Nothing was launched.")
        return BROWSERS[key]


class DebugBrowserStarter:
    """IBrowserStarter: browser_session's launcher, with the table's executable and profile.

    Starts Chrome through WSL interop, so it opens in the session of whichever `wslhost` carries the
    interop socket -- session 0 (hidden) when WSL was started remotely. The plane uses
    `InteractiveTaskStarter` instead; this stays for a WSL started from the person's own desktop."""

    def __init__(self) -> None:
        self._spawner = DetachedProcessSpawner()
        self._profiles: dict[str, str] = {}

    def start(self, kind: BrowserKind, url: str) -> tuple[bool, str]:
        if not Path(kind.exe).exists():
            return False, f"{kind.name} is not installed where expected ({kind.exe})"
        profile = self._profiles.setdefault(kind.name, WindowsLocalAppDataProfile(kind.profile).resolve())
        base = ChromeCommandBuilder().executable(kind.exe).profile(profile).flag("--no-first-run")
        done = DebugChromeLauncher(base, self._spawner).launch(url, DebugEndpoint(port=kind.port))
        return done.launched, done.reason


class CdpBrowserEndpoint:
    """IBrowserEndpoint over cdp_browser's loopback-only target directory."""

    def __init__(self) -> None:
        self._directory = HttpTargetDirectory(LoopbackOnly(), timeout_seconds=3.0)

    def reachable(self, kind: BrowserKind) -> tuple[bool, str]:
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{kind.port}/json/version", timeout=2.0) as response:
                return True, str(json.loads(response.read().decode()).get("Browser", ""))
        except (OSError, ValueError) as exc:
            return False, f"nothing answered on 127.0.0.1:{kind.port} ({type(exc).__name__})"

    def pages(self, kind: BrowserKind) -> list[CdpTarget]:
        return self._directory.pages(DebugEndpoint(port=kind.port))

    def open_page(self, kind: BrowserKind, url: str) -> CdpTarget:
        return self._directory.open_page(DebugEndpoint(port=kind.port), url)

    def activate(self, kind: BrowserKind, target_id: str) -> None:
        self._directory.activate(DebugEndpoint(port=kind.port), target_id)

    def close_page(self, kind: BrowserKind, target_id: str) -> None:
        self._directory.close_page(DebugEndpoint(port=kind.port), target_id)

    def shutdown(self, kind: BrowserKind) -> None:
        """Browser.close over the browser-level socket. Chrome may drop the socket before it answers."""
        channel = WebSocketCdpConnector(timeout_seconds=5.0).connect(self._directory.browser_socket(DebugEndpoint(port=kind.port)))
        try:
            channel.call("Browser.close", timeout_seconds=5.0)
        except (BrowserWorkflowError, OSError):
            pass
        finally:
            try:
                channel.close()
            except OSError:
                pass


class CdpPageAttacher:
    """IPageAttacher: the named tab, else the most recently used one."""

    def attach(self, kind: BrowserKind, tab_id: str | None) -> IPageSession:
        directory = HttpTargetDirectory(LoopbackOnly())
        chooser = PreferredTargetChooser(lambda: (tab_id or "", [])) if tab_id else FirstPageChooser()
        endpoint = DebugEndpoint(port=kind.port)
        target = chooser.choose(directory.pages(endpoint))
        if target is None:
            raise BrowserWorkflowError(f"{kind.name} has no open web page to use")
        if tab_id and target.id != tab_id:
            raise BrowserWorkflowError(f"no open tab {tab_id}")
        return CdpWorkflowBrowser(endpoint, target, directory, WebSocketCdpConnector())


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class ControlPlane:
    catalog:  IBrowserCatalog
    starter:  IBrowserStarter
    endpoint: IBrowserEndpoint
    attacher: IPageAttacher
    clock:    IClock
    link:     IWindowsLink
    shots:    Path                   # where screenshots land (a folder this ADW owns)
    ready_seconds: float = 20.0
    sessions: IWindowSessions | None = None   # None: visibility cannot be checked, so open refuses to claim it
    #: browser.download's three parts. All None on a plane built without them, and that operation then
    #: refuses rather than pressing something that saves a file and guessing whether it worked.
    folder:   IDownloadFolder | None = None     # where Chrome saves (a path on Chrome's machine)
    watcher:  IDownloadWatcher | None = None    # the same folder as this machine sees it
    verifier: IDownloadVerifier | None = None   # what is wrong with what landed, if anything


def default_plane() -> ControlPlane:
    """The composition root: the only place in Browser Control that names a concrete part.

    Downloads land in the one Chrome-side folder this system already uses (NativeDownloadDirectory,
    shared with playback), so there is a single place to look for a saved file, and
    BROWSER_WORKFLOW_DOWNLOAD_DIR moves it for both at once."""
    root = Path(__file__).resolve().parent.parent / "adw_data" / "browser_control" / "screenshots"
    folder = NativeDownloadDirectory()
    return ControlPlane(StaticBrowserCatalog(), InteractiveTaskStarter(), CdpBrowserEndpoint(), CdpPageAttacher(),
                        SystemClock(), WslInteropLink(), root, sessions=WindowsSessionProbe(),
                        folder=folder, watcher=FolderDownloadWatcher(folder.resolve),
                        verifier=FileDownloadVerifier())


def checked_url(raw: object) -> str:
    """http(s) with a host, or BrowserWorkflowError. file:, javascript:, chrome: and the like are never opened."""
    text = str(raw or "").strip()
    if text and "://" not in text and re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/.*)?", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise BrowserWorkflowError(f"I only open http or https links with a host, not {text[:80]!r}")
    return text

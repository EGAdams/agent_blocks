"""browser.download: press one element and keep the file it saves.

This is the operation browser.click deliberately is not. A click that triggers a download
reports the click honestly and then nothing arrives, because Chrome only writes a file when a
download folder has been set for the session -- and `browser_click` never sets one, so a
`.tif` anchor on Michigan's plat site answered "Clicked '0337201.tif'." with an empty folder
behind it. Setting that behaviour inside `click` would mean every click an agent makes could
write to EG's disk; making it its own operation means the saving is the thing that was asked
for, visible in the name, the task and the audit log.

Nothing here is new machinery. The folder is `browser_workflows`' `NativeDownloadDirectory`
(the one Chrome-side folder this system downloads into, shared with playback), the waiting and
checking are Cashy's `FolderDownloadWatcher` and `FileDownloadVerifier`, the element is found
by the same `RankedElementResolver` as `click`, and the consequence check is the same
classifier -- a "Download and pay" button still needs confirm=true.

The one rule it exists to enforce: **a click that saved no file is never reported as a
download.** It says what it clicked, where it looked, and what else the click may have done
instead (opened a tab, or moved the page), which is what tells a caller which half failed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import RecordedAction
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.operations.common import OperationOutcome, locators_from, ok, refused
from browser_workflows.playback.locating import RankedElementResolver, default_strategy_registry

from .interfaces import IDownloadFolder, IDownloadVerifier, IDownloadWatcher
from .operations import BrowserOperation, safe_url
from .plane import ControlPlane

#: How long to wait for Chrome to finish writing. A scanned plat sheet is a few hundred KB over a
#: slow government host; a bank statement can be slower. Beyond this it is reported as not arrived,
#: never as saved.
WAIT_SECONDS = 60.0
#: Reported for each file, so a caller can tell a real document from an error page saved as one.
HEAD_BYTES = 8


class Download(BrowserOperation):
    name = "browser_download"
    description = ("browser.download: press one element (text, label or css) and keep the file it saves, "
                   "reporting the name, size and type. A click that saves nothing is never called a download")
    #: It writes a file to EG's machine, so it is recorded as a change, not a reading.
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
        folder, watcher, verifier = self._downloads()
        # Asking Windows where its Downloads folder is means running cmd.exe, and that needs a WSL interop
        # socket. Attaching to an already-open tab is plain HTTP and needs none, so nothing had ensured one
        # yet: under the Bridge worker (a systemd service, no WSL_INTEROP) cmd.exe exited 1 and the download
        # was refused. `link` finds a socket that works -- it is the same check `open` does before launching.
        self._plane.link.ensure()
        try:
            directory = folder.resolve()
        except BrowserWorkflowError as exc:
            return refused(f"I cannot tell Chrome where to save: {exc}", browser=kind.name, verified=False)
        page = self._page(kind, args)
        configure = getattr(page, "configure_downloads", None)
        if configure is None:
            page.release()
            return refused("this browser cannot be told where to save downloads, so I will not press something "
                           "that saves a file and then guess whether it worked.", browser=kind.name, verified=False)
        try:
            configure(directory)
            element, resolution = self._resolver.resolve(page, candidates)
            if element is None:
                tried = ", ".join(f"{a.label}: {a.outcome}" for a in resolution.attempts)
                return refused(f"I could not find one element to press for that ({tried}). Run "
                               "browser.form_controls and repeat with --css taken from the row you mean.",
                               browser=kind.name, verified=False)
            label = (element.text or "")[:60]
            before, was_at = watcher.before(), page.current_url
            element.click()
            saved = watcher.wait_new(before, WAIT_SECONDS)
            now_at = page.current_url
        finally:
            page.release()
        moved = safe_url(now_at) if now_at != was_at else ""
        if not saved:
            return refused(f"I pressed {label!r}, but no file arrived in {directory} within "
                           f"{WAIT_SECONDS:.0f}s."
                           + (f" The page moved to {moved} instead, so it opened rather than downloaded."
                              if moved else " The page did not move, so the press may have reached nothing.")
                           + " browser.read shows what the tab holds now.",
                           browser=kind.name, clicked=label, moved_to=moved,
                           download_directory=directory, verified=False)
        files = [self._describe(path, verifier) for path in saved]
        bad = [f["problem"] for f in files if f["problem"]]
        line = f"Saved {self._listing(files)} to {directory}."
        if bad:
            return refused(f"I pressed {label!r} and a file arrived, but {' '.join(bad)}", browser=kind.name,
                           clicked=label, download_directory=directory, files=files, verified=False)
        return ok(line, browser=kind.name, clicked=label, moved_to=moved, download_directory=directory,
                  files=files, verified=True)

    def _downloads(self) -> tuple[IDownloadFolder, IDownloadWatcher, IDownloadVerifier]:
        parts = (self._plane.folder, self._plane.watcher, self._plane.verifier)
        if any(p is None for p in parts):
            raise BrowserWorkflowError("this plane has no download folder wired, so nothing can be saved")
        return parts                                        # type: ignore[return-value]

    @staticmethod
    def _describe(path: Path, verifier: IDownloadVerifier) -> dict[str, Any]:
        """Name, size, first bytes and what is wrong with it -- never the contents."""
        size = path.stat().st_size if path.is_file() else 0
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES).hex() if size else ""
        return {"name": path.name, "path": str(path), "bytes": size, "head": head,
                "problem": verifier.problem(path)}

    @staticmethod
    def _listing(files: list[dict[str, Any]]) -> str:
        return ", ".join(f"{f['name']} ({f['bytes'] / 1024:.1f} KB)" for f in files)

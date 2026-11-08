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
checking are Cashy's `FolderDownloadWatcher` (inside `DownloadFiler`) and `FileDownloadVerifier`, the element is found
by the same `RankedElementResolver` as `click`, and the consequence check is the same
classifier -- a "Download and pay" button still needs confirm=true.

The one rule it exists to enforce: **a click that saved no file is never reported as a
download.** It says what it clicked, where it looked, and what else the click may have done
instead (opened a tab, or moved the page), which is what tells a caller which half failed.

Two more follow from it. **Nothing is overwritten:** Chrome replaces a same-named file in its
download folder, so each press saves into its own empty `.incoming/<random>` folder and the file
is then moved into the download folder under a free name (`0337201 (2).tif`). And **a saved file
is something a reader can look at:** each one is reported with its SHA-256 and its real type (from
its bytes, so a sign-in page saved as `.tif` is caught), and `file_inspection` renders its pages
to images and pulls its text, which the Bridge worker publishes so ChatGPT can view them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import RecordedAction
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.operations.common import OperationOutcome, locators_from, ok, refused
from browser_workflows.playback.locating import RankedElementResolver, default_strategy_registry

from browser_workflows.playback.downloads import KeptFile, sha256
from file_inspection.sniff import sniff_mime

from .interfaces import IDownloadFiler, IDownloadFolder, IDownloadVerifier
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
                   "reporting the name, size, SHA-256 and real type, never overwriting an earlier file, and "
                   "rendering its pages for viewing. A click that saves nothing is never called a download")
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
        folder, filer, verifier = self._downloads()
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
            staged = filer.stage(directory)
        except OSError as exc:
            page.release()
            return refused(f"I cannot make a folder to save into under {directory}: {exc}", browser=kind.name,
                           download_directory=directory, verified=False)
        # Chrome writes into this download's own empty folder, so a same-named file from an earlier
        # download cannot be overwritten, and whatever lands there is this press's file and no other's.
        kept, leftovers, now_at, was_at, label = [], [], "", "", ""
        try:
            configure(staged.chrome_dir)
            element, resolution = self._resolver.resolve(page, candidates)
            if element is None:
                tried = ", ".join(f"{a.label}: {a.outcome}" for a in resolution.attempts)
                return refused(f"I could not find one element to press for that ({tried}). Run "
                               "browser.form_controls and repeat with --css taken from the row you mean.",
                               browser=kind.name, verified=False)
            label = (element.text or "")[:60]
            before, was_at = staged.before(), page.current_url
            element.click()
            saved = staged.wait_new(before, WAIT_SECONDS)
            now_at = page.current_url
            kept = staged.keep(saved)
        finally:
            try:
                configure(directory)                        # anything downloaded later goes to the folder itself
            except Exception:                               # the tab may have navigated away; never mask the result
                pass
            page.release()
            leftovers = staged.finish()
        moved = safe_url(now_at) if now_at != was_at else ""
        if not kept:
            partial = [n for n in leftovers if n.endswith(".crdownload")]
            why = (f" A download started ({', '.join(partial)}) but Chrome had not finished it; it is left in "
                   f"{staged.chrome_dir} rather than reported as saved." if partial else
                   f" The page moved to {moved} instead, so it opened rather than downloaded." if moved else
                   " The page did not move, so the press may have reached nothing.")
            return refused(f"I pressed {label!r}, but no file arrived in {directory} within "
                           f"{WAIT_SECONDS:.0f}s.{why} browser.read shows what the tab holds now.",
                           browser=kind.name, clicked=label, moved_to=moved, download_directory=directory,
                           unfinished=leftovers, verified=False)
        files = [self._describe(k, verifier) for k in kept]
        bad = [f["problem"] for f in files if f["problem"]]
        if bad:
            return refused(f"I pressed {label!r} and a file arrived, but {' '.join(bad)}. It is kept, under "
                           f"{self._listing(files)}, so the page it really is can be read.", browser=kind.name,
                           clicked=label, download_directory=directory, files=files, verified=False)
        renamed = [f for f in files if f["renamed"]]
        line = f"Saved {self._listing(files)} to {directory}."
        if renamed:
            line += " " + " ".join(f"A file named {f['original_name']} was already there, so this one is "
                                   f"{f['name']}." for f in renamed)
        return ok(line, browser=kind.name, clicked=label, moved_to=moved, download_directory=directory,
                  files=files, verified=True)

    def _downloads(self) -> tuple[IDownloadFolder, IDownloadFiler, IDownloadVerifier]:
        parts = (self._plane.folder, self._plane.filer, self._plane.verifier)
        if any(p is None for p in parts):
            raise BrowserWorkflowError("this plane has no download folder wired, so nothing can be saved")
        return parts                                        # type: ignore[return-value]

    def _describe(self, kept: KeptFile, verifier: IDownloadVerifier) -> dict[str, Any]:
        """Name, size, checksum, real type, first bytes, what is wrong with it, and -- when an inspector
        is wired -- its pages as images and its text. The original file is never changed."""
        path = Path(kept.path)
        size = path.stat().st_size if path.is_file() else 0
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES).hex() if size else ""
        digest = sha256(path)
        mime = sniff_mime(path) if size else "application/octet-stream"
        problem = verifier.problem(path)
        out: dict[str, Any] = {**kept.model_dump(), "bytes": size, "sha256": digest, "mime": mime, "head": head,
                               "status": "suspect" if problem else "saved", "problem": problem}
        inspector = self._plane.inspector
        if inspector is not None and size:
            out["inspection"] = inspector.inspect(path, digest).model_dump()
        return out

    @staticmethod
    def _listing(files: list[dict[str, Any]]) -> str:
        return ", ".join(f"{f['name']} ({f['bytes'] / 1024:.1f} KB)" for f in files)

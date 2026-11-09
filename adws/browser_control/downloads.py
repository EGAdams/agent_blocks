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

**A bare address works too** (`url` in place of text/label/css), for a file that no link on any open
page names -- a scanned plat sheet's direct address, a statement link from an email. It is asked for
in a tab of its own (the caller's tab is never moved), in the same signed-in Chrome, so the site sees
the person's own session and no cookie or password passes through here. What comes back is held to
the same rule: a file that saved, or a plain statement that none did. A web page in its place is
never kept as the download; if that page asks for a password it is left open and brought to the front
for EG to sign in -- reported as `sign_in_required`, never worked around -- and any other page is
closed again. The address's query string can carry a token, so only scheme, host and path are reported.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.models import RecordedAction
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.operations.common import OperationOutcome, locators_from, ok, refused
from browser_workflows.playback.locating import RankedElementResolver, default_strategy_registry

from browser_workflows.playback.downloads import KeptFile, sha256
from file_inspection.sniff import sniff_mime

from .interfaces import IDownloadFiler, IDownloadFolder, IDownloadVerifier
from .operations import BrowserOperation, safe_url
from .plane import ControlPlane, checked_url

#: How long to wait for Chrome to finish writing. A scanned plat sheet is a few hundred KB over a
#: slow government host; a bank statement can be slower. Beyond this it is reported as not arrived,
#: never as saved.
WAIT_SECONDS = 60.0
#: Reported for each file, so a caller can tell a real document from an error page saved as one.
HEAD_BYTES = 8


class Download(BrowserOperation):
    name = "browser_download"
    description = ("browser.download: press one element (text, label or css) -- or ask for a bare http(s) "
                   "address (url) in a tab of its own -- and keep the file it saves, reporting the name, size, SHA-256 and real type, never overwriting an earlier file, and "
                   "rendering its pages for viewing. A click that saves nothing is never called a download")
    #: It writes a file to EG's machine, so it is recorded as a change, not a reading.
    destructive = True

    def __init__(self, plane: ControlPlane) -> None:
        super().__init__(plane)
        self._resolver = RankedElementResolver(default_strategy_registry())
        self._classifier = KeywordConsequenceClassifier()

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        kind = self._kind(args)
        if args.get("url"):
            if any(args.get(k) for k in ("css", "label", "text")):
                return refused("give either an address (url) or something on the page to press (text, label or "
                               "css), not both -- I will not guess which one you meant.", browser=kind.name,
                               verified=False)
            return self._from_url(kind, args)
        candidates = locators_from(args)
        hint = " ".join(str(args.get(k) or "") for k in ("text", "label"))
        why = self._classifier.reason(RecordedAction(action_type="click", metadata={"text": hint}))
        if why and not args.get("confirm"):
            return refused(f"I did not click: {why}. Ask EG, then repeat with confirm=true.", browser=kind.name,
                           needs_confirmation=True, verified=False)
        prepared = self._prepare(kind)
        if isinstance(prepared, OperationOutcome):
            return prepared
        directory, filer, verifier = prepared
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
            if not saved and now_at != was_at and self._save_shown(page):
                saved = staged.wait_new(before, WAIT_SECONDS)
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
        return self._report(kind, kept, verifier, directory, f"I pressed {label!r}", clicked=label, moved_to=moved)

    def _prepare(self, kind: Any) -> tuple[str, IDownloadFiler, IDownloadVerifier] | OperationOutcome:
        """The download folder (Chrome's spelling), the filer and the verifier -- or the refusal saying why not."""
        folder, filer, verifier = self._downloads()
        # Asking Windows where its Downloads folder is means running cmd.exe, and that needs a WSL interop
        # socket. Attaching to an already-open tab is plain HTTP and needs none, so nothing had ensured one
        # yet: under the Bridge worker (a systemd service, no WSL_INTEROP) cmd.exe exited 1 and the download
        # was refused. `link` finds a socket that works -- it is the same check `open` does before launching.
        self._plane.link.ensure()
        try:
            return folder.resolve(), filer, verifier
        except BrowserWorkflowError as exc:
            return refused(f"I cannot tell Chrome where to save: {exc}", browser=kind.name, verified=False)

    def _from_url(self, kind: Any, args: dict[str, Any]) -> OperationOutcome:
        """Ask for `url` in a tab of its own and keep the file it answers with (see the module docstring)."""
        try:
            url = checked_url(args.get("url"))
        except BrowserWorkflowError as exc:
            return refused(f"I did not ask for that address: {exc}.", browser=kind.name, verified=False)
        shown = safe_url(url)
        why = self._classifier.reason(RecordedAction(action_type="click", metadata={"text": urlparse(url).path}))
        if why and not args.get("confirm"):
            return refused(f"I did not ask for {shown}: {why}. Ask EG, then repeat with confirm=true.",
                           browser=kind.name, needs_confirmation=True, verified=False)
        prepared = self._prepare(kind)
        if isinstance(prepared, OperationOutcome):
            return prepared
        directory, filer, verifier = prepared
        self._up(kind)
        tab = self._plane.endpoint.open_page(kind, "about:blank")
        keep_tab, kept, leftovers, failure, title, sign_in = False, [], [], "", "", False
        page = self._plane.attacher.attach(kind, tab.id)
        configure = getattr(page, "configure_downloads", None)
        try:
            if configure is None:
                return refused("this browser cannot be told where to save downloads, so I will not ask for a file "
                               "and then guess whether it arrived.", browser=kind.name, verified=False)
            try:
                staged = filer.stage(directory)
            except OSError as exc:
                return refused(f"I cannot make a folder to save into under {directory}: {exc}", browser=kind.name,
                               download_directory=directory, verified=False)
            try:
                configure(staged.chrome_dir)
                before = staged.before()
                try:
                    page.navigate(url)
                except BrowserWorkflowError as exc:     # Chrome ends a navigation that became a download with
                    failure = str(exc)                  # net::ERR_ABORTED -- so only a missing file decides
                saved = staged.wait_new(before, WAIT_SECONDS)
                if not saved and self._save_shown(page):    # a PDF shown in Chrome's viewer
                    saved = staged.wait_new(before, WAIT_SECONDS)
                kept = staged.keep(saved)
                if not kept:
                    title = (page.title or "")[:80]
                    sign_in = self._asks_for_password(page)
                    keep_tab = sign_in
            finally:
                try:
                    configure(directory)
                except Exception:
                    pass
                leftovers = staged.finish()
        finally:
            page.release()
            if keep_tab:
                self._plane.endpoint.activate(kind, tab.id)
            else:
                try:
                    self._plane.endpoint.close_page(kind, tab.id)
                except BrowserWorkflowError:
                    pass
        if kept:
            return self._report(kind, kept, verifier, directory, f"I asked for {shown}", url=shown)
        partial = [n for n in leftovers if n.endswith(".crdownload")]
        base = dict(browser=kind.name, url=shown, download_directory=directory, unfinished=leftovers, verified=False)
        if partial:
            return refused(f"I asked for {shown} and a download started ({', '.join(partial)}), but Chrome had not "
                           f"finished it within {WAIT_SECONDS:.0f}s; it is left in {staged.chrome_dir} rather than "
                           "reported as saved.", **base)
        if sign_in:
            return refused(f"{shown} answered with a sign-in page ({title!r}), not a file. I did not sign in and "
                           f"will not: it is open in tab {tab.id} for EG to sign in, and then this download can be "
                           "asked for again.", cause="sign_in_required", needs_person=True, tab=tab.id, **base)
        if failure and "ERR_ABORTED" not in failure:
            return refused(f"Chrome could not fetch {shown}: {failure.split(': ', 1)[-1][:160]}. Nothing was saved.",
                           cause="fetch_failed", **base)
        return refused(f"{shown} answered with a web page ({title!r}), not a file, so nothing was saved. "
                       "browser.open shows the page if that is what was wanted.", cause="not_a_file", **base)

    def _report(self, kind: Any, kept: list[KeptFile], verifier: IDownloadVerifier, directory: str, did: str,
                **extra: Any) -> OperationOutcome:
        """The files that arrived, described, checked, and -- for an archive -- unpacked."""
        files = [self._describe(k, verifier) for k in kept]
        bad = [f["problem"] for f in files if f["problem"]]
        if bad:
            return refused(f"{did} and a file arrived, but {' '.join(bad)}. It is kept, under "
                           f"{self._listing(files)}, so the page it really is can be read.", browser=kind.name,
                           download_directory=directory, files=files, verified=False, **extra)
        renamed = [f for f in files if f["renamed"]]
        line = f"Saved {self._listing(files)} to {directory}."
        if renamed:
            line += " " + " ".join(f"A file named {f['original_name']} was already there, so this one is "
                                   f"{f['name']}." for f in renamed)
        files, unpacked = self._with_members(files)
        if unpacked:
            line += " " + unpacked
        return ok(line, browser=kind.name, download_directory=directory, files=files, verified=True, **extra)

    @staticmethod
    def _asks_for_password(page: Any) -> bool:
        """A visible password box is the sign of a sign-in page. Its value is never read."""
        try:
            return any(e.is_displayed() for e in page.find('input[type="password"]'))
        except BrowserWorkflowError:
            return False

    @staticmethod
    def _save_shown(page: Any) -> bool:
        """The press opened a file in the tab instead of saving it -- Chrome shows a PDF in its viewer
        unless told otherwise, so a "Form W-9 PDF" link moved the page and saved nothing. Ask the tab to
        save what it is showing, with its own sign-in. A tab showing an ordinary web page is left alone:
        saving a page someone navigated to would be reporting the wrong thing as the download."""
        save = getattr(page, "save_shown_file", None)
        try:
            return bool(save()) if save is not None else False
        except BrowserWorkflowError:
            return False

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
    def _with_members(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """Each archive's safely unpacked files (file_inspection's SafeZipUnpacker) listed after it as files
        of their own, so each can be published and viewed like any download. The archive's own entry keeps
        only a count; nothing in a member was run, and programs and nested archives were never written out."""
        out: list[dict[str, Any]] = []
        said: list[str] = []
        for f in files:
            inspection = f.get("inspection") or {}
            members = inspection.get("members") or []
            if inspection:
                f = {**f, "inspection": {k: v for k, v in inspection.items() if k != "members"},
                     "unpacked_members": len(members)}
            out.append(f)
            for m in members:
                out.append({"original_name": f"{f['name']} > {m['name']}", "name": Path(m["name"]).name,
                            "path": m["path"], "chrome_path": "", "renamed": False, "bytes": m["bytes"],
                            "sha256": m["sha256"], "mime": m["inspection"]["mime"], "head": "",
                            "status": "unpacked", "problem": "", "from_archive": f["name"],
                            "archive_member": m["name"], "inspection": m["inspection"]})
            if inspection.get("kind") == "archive":
                said.append(f"From {f['name']}: {inspection.get('note') or 'nothing unpacked'}.")
        return out, " ".join(said)

    @staticmethod
    def _listing(files: list[dict[str, Any]]) -> str:
        return ", ".join(f"{f['name']} ({f['bytes'] / 1024:.1f} KB)" for f in files)

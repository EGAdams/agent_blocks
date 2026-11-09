"""browser.download over fake interfaces: a file that lands, a file that does not, and a file that is wrong.

The one rule under test: a press that saved nothing is never reported as a download.

    cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests/test_downloads.py
"""
from __future__ import annotations

from pathlib import Path

from browser_control.filelinks import saved_extension
from .test_operations import FakeElement, FakeLink, make, run


class FakeFolder:
    """IDownloadFolder. `fails` is a folder that cannot be found at all."""

    def __init__(self, directory=r"C:\Users\eg\Downloads\BrowserRecorder", fails=""):
        self.directory, self.fails = directory, fails

    def resolve(self):
        if self.fails:
            from browser_workflows.errors import BrowserWorkflowError
            raise BrowserWorkflowError(self.fails)
        return self.directory


class FakeWatcher:
    """IDownloadFiler and its IStagedDownload in one: hands back whatever files the test said would
    arrive, and keeps them in place (the real move-and-rename is tested in browser_workflows)."""

    chrome_dir = r"C:\Users\eg\Downloads\BrowserRecorder\.incoming\abc123"

    def __init__(self, arriving=(), leftovers=()):
        self.arriving, self.waited, self.leftovers = list(arriving), None, list(leftovers)
        self.staged_under, self.finished = None, False

    def stage(self, directory):
        self.staged_under = directory
        return self

    def before(self):
        return set()

    def wait_new(self, before, timeout_seconds=30.0):
        self.waited = timeout_seconds
        return list(self.arriving)

    def keep(self, files):
        from browser_workflows.playback.downloads import KeptFile
        return [KeptFile(original_name=p.name, name=p.name, path=str(p), chrome_path=p.name, renamed=False)
                for p in files]

    def finish(self):
        self.finished = True
        return self.leftovers


class FakeInspector:
    def __init__(self):
        self.seen = []

    def inspect(self, source, sha256):
        from file_inspection.models import Inspection
        self.seen.append((source.name, sha256))
        return Inspection(kind="image", mime="image/tiff", page_count=1)


class FakeVerifier:
    def __init__(self, problems=None):
        self.problems = problems or {}

    def problem(self, path):
        return self.problems.get(path.name, "")


def wired(tmp_path, arriving=(), problems=None, folder=None, leftovers=(), inspector=None, **world_args):
    folder = folder or FakeFolder()
    watcher = FakeWatcher(arriving, leftovers)
    ops, world = make(tmp_path, downloads=(folder, watcher, FakeVerifier(problems), inspector), **world_args)
    world.elements = [FakeElement(text="0337201.tif", attrs={"href": "/docs/0337201.tif"})]
    return ops, world, watcher


def a_file(tmp_path, name="0337201.tif", body=b"II*\x00rest-of-a-tiff"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


# -- what lands -------------------------------------------------------------------


def test_windows_interop_is_ensured_before_the_folder_is_asked_for(tmp_path):
    """Finding the Downloads folder runs cmd.exe. Attaching to an open tab does not, so nothing had
    ensured a WSL interop socket: under the Bridge worker cmd.exe exited 1 and the download was refused."""
    link = FakeLink()
    ops, world = make(tmp_path, link=link, running=["chrome"],
                      downloads=(FakeFolder(), FakeWatcher([a_file(tmp_path)]), FakeVerifier()))
    world.elements = [FakeElement(text="0337201.tif", attrs={"href": "/docs/0337201.tif"})]
    out = run(ops, "browser.download", text="0337201.tif")
    assert out.ok and link.ensured == 1


def test_a_file_that_arrives_is_reported_with_its_name_and_size(tmp_path):
    saved = a_file(tmp_path)
    ops, world, watcher = wired(tmp_path, arriving=[saved], running=("chrome",))
    out = run(ops, "browser.download", text="0337201.tif")
    assert out.ok and out.data["verified"] is True
    assert out.data["files"][0]["name"] == "0337201.tif"
    assert out.data["files"][0]["bytes"] == saved.stat().st_size
    assert out.data["files"][0]["head"] == b"II*\x00rest".hex()[:16]
    assert "0337201.tif" in out.line
    import hashlib
    assert out.data["files"][0]["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    assert out.data["files"][0]["mime"] == "image/tiff" and out.data["files"][0]["status"] == "saved"


def test_chrome_is_told_where_to_save_before_the_press_then_pointed_back(tmp_path):
    ops, world, watcher = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    run(ops, "browser.download", text="0337201.tif")
    #: The whole bug: without this call Chrome writes nothing and the click still looks successful.
    #: It saves into this download's own staging folder, and is then pointed back at the folder itself.
    assert world.download_dirs == [FakeWatcher.chrome_dir, FakeFolder().directory]
    assert watcher.staged_under == FakeFolder().directory and watcher.finished


def test_each_file_is_inspected_when_an_inspector_is_wired(tmp_path):
    saved = a_file(tmp_path)
    inspector = FakeInspector()
    ops, *_ = wired(tmp_path, arriving=[saved], inspector=inspector, running=("chrome",))
    out = run(ops, "browser.download", text="0337201.tif")
    assert inspector.seen == [("0337201.tif", out.data["files"][0]["sha256"])]
    assert out.data["files"][0]["inspection"]["page_count"] == 1


def test_an_archives_unpacked_files_follow_it_as_files_of_their_own(tmp_path):
    import zipfile
    from file_inspection.inspector import default_inspector
    archive = tmp_path / "plats.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("deeds/notes.txt", "Liber 4 of Plats, page 31")
        z.writestr("setup.exe", b"MZ")
    ops, *_ = wired(tmp_path, arriving=[archive], inspector=default_inspector(tmp_path / "insp"), running=("chrome",))
    out = run(ops, "browser.download", text="0337201.tif")
    assert out.ok, out.line
    first, member = out.data["files"]
    assert first["name"] == "plats.zip" and first["unpacked_members"] == 1
    assert "members" not in first["inspection"] and first["inspection"]["kind"] == "archive"
    assert member["name"] == "notes.txt" and member["from_archive"] == "plats.zip"
    assert member["archive_member"] == "deeds/notes.txt" and member["status"] == "unpacked"
    assert member["inspection"]["kind"] == "text" and "Liber 4" in member["inspection"]["text"]
    assert "From plats.zip: unpacked 1 file(s); 1 left in the archive." in out.line


def test_an_unfinished_download_is_named_not_called_saved(tmp_path):
    ops, *_ = wired(tmp_path, leftovers=["0337201.tif.crdownload"], running=("chrome",))
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and "had not finished" in out.line
    assert out.data["unfinished"] == ["0337201.tif.crdownload"]


def test_the_folder_is_restored_even_when_nothing_is_found(tmp_path):
    ops, world, watcher = wired(tmp_path, running=("chrome",))
    world.elements = []
    run(ops, "browser.download", text="0337201.tif")
    assert world.download_dirs[-1] == FakeFolder().directory and watcher.finished


def test_the_tab_is_released_and_never_quit(tmp_path):
    ops, world, _ = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    run(ops, "browser.download", text="0337201.tif")
    assert world.page.released and world.shutdowns == []


# -- what does not land -----------------------------------------------------------


def test_nothing_arriving_is_refused_not_called_a_download(tmp_path):
    ops, world, watcher = wired(tmp_path, running=("chrome",))
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and out.data["verified"] is False
    assert "no file arrived" in out.line and watcher.waited == 60.0


def test_a_press_that_navigated_instead_says_so(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",))
    world.elements = [FakeElement(text="0337201.tif", sets=None)]
    page_moved = {"done": False}

    def click_that_navigates():
        world.page.current_url = "https://plats.example/view?id=1"
        page_moved["done"] = True

    out_ops = ops
    world.elements[0].click = click_that_navigates
    out = run(out_ops, "browser.download", text="0337201.tif")
    assert page_moved["done"] and not out.ok
    assert "opened rather than downloaded" in out.line
    assert out.data["moved_to"].startswith("https://plats.example/view?")


def test_a_press_that_opened_a_pdf_saves_what_the_tab_shows(tmp_path):
    """IRS's "Form W-9 PDF" link, live: Chrome showed the PDF in its viewer and saved nothing."""
    pdf = a_file(tmp_path, "fw9.pdf", b"%PDF-1.7 rest")
    ops, world, watcher = wired(tmp_path, running=("chrome",))
    world.elements = [FakeElement(text="Form W-9 PDF", sets=None)]
    asked = []
    arrivals = iter([[], [pdf]])
    watcher.wait_new = lambda before, timeout_seconds=30.0: next(arrivals)

    def click_that_opens_the_viewer():
        world.page.current_url = "https://irs.example/pub/fw9.pdf"
        world.page.save_shown_file = lambda: asked.append(True) or True

    world.elements[0].click = click_that_opens_the_viewer
    out = run(ops, "browser.download", text="Form W-9 PDF")
    assert asked == [True] and out.ok and out.data["verified"] is True
    assert out.data["files"][0]["name"] == "fw9.pdf" and out.data["files"][0]["mime"] == "application/pdf"


def test_a_press_that_opened_a_web_page_is_not_saved_as_the_download(tmp_path):
    ops, world, watcher = wired(tmp_path, running=("chrome",))
    world.elements = [FakeElement(text="0337201.tif", sets=None)]

    def click_that_navigates():
        world.page.current_url = "https://plats.example/view?id=1"
        world.page.save_shown_file = lambda: False       # an HTML page: nothing to save

    world.elements[0].click = click_that_navigates
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and "opened rather than downloaded" in out.line


def test_a_plane_with_no_download_parts_refuses(tmp_path):
    ops, _ = make(tmp_path, running=("chrome",))
    out = ops["browser_download"].run({"text": "0337201.tif"})
    assert not out.ok and "nothing can be saved" in out.line


def test_a_browser_that_cannot_be_told_where_to_save_is_not_pressed(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",), )
    world.can_download = False
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and "cannot be told where to save downloads" in out.line
    assert world.elements[0].clicked is False


def test_a_folder_that_cannot_be_found_refuses_before_pressing(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",), folder=FakeFolder(fails="no Windows user profile"))
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and "no Windows user profile" in out.line
    assert world.elements[0].clicked is False


def test_an_element_that_cannot_be_found_names_form_controls(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",))
    world.elements = []
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and "browser.form_controls" in out.line


# -- what lands but is wrong ------------------------------------------------------


def test_a_file_the_verifier_objects_to_is_not_called_saved(tmp_path):
    saved = a_file(tmp_path, body=b"")
    ops, *_ = wired(tmp_path, arriving=[saved], running=("chrome",),
                    problems={"0337201.tif": "it is empty (0 bytes)."})
    out = run(ops, "browser.download", text="0337201.tif")
    assert not out.ok and out.data["verified"] is False and "it is empty" in out.line


# -- consequential words ----------------------------------------------------------


def test_a_consequential_word_needs_confirmation(tmp_path):
    ops, world, _ = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    world.elements = [FakeElement(text="Pay and download receipt")]
    out = run(ops, "browser.download", text="Pay and download receipt")
    assert not out.ok and out.data.get("needs_confirmation") is True
    assert world.elements[0].clicked is False


# -- which links save a file ------------------------------------------------------


def test_a_postback_link_is_recognised_by_its_text(tmp_path):
    """Accela's .tif link has no file name in its href at all, only in the row's text."""
    element = FakeElement(text="0337201.tif", attrs={
        "href": "javascript:__doPostBack('attachmentList$gdvAttachmentList$ctl02$lnkFileName','')"})
    assert saved_extension(element) == "tif"


def test_a_download_attribute_counts_even_with_an_odd_name():
    assert saved_extension(FakeElement(text="Get it", tag="a", attrs={"download": "report.xlsx"})) == "xlsx"
    assert saved_extension(FakeElement(text="Get it", tag="a", attrs={"download": ""})) == "file"


def test_an_ordinary_page_link_does_not_count():
    for href, text in (("/Records/Detail.aspx?id=7", "Record 0337201"),
                       ("https://x.example/index.php", "Home"),
                       ("/about.html", "About us")):
        assert saved_extension(FakeElement(text=text, attrs={"href": href})) == ""


def test_clicking_a_file_link_is_refused_and_names_the_download_operation(tmp_path):
    ops, world = make(tmp_path, running=("chrome",))
    world.elements = [FakeElement(text="0337201.tif", attrs={"href": "/docs/0337201.tif"})]
    out = run(ops, "browser.click", text="0337201.tif")
    assert not out.ok and out.data["use_instead"] == "browser.download"
    assert out.data["saves_file"] == "tif" and world.elements[0].clicked is False


# -- a bare address -----------------------------------------------------------------

PLAT_URL = "https://plats.example/files/0337201.tif?session=SECRET"


def test_a_bare_address_is_saved_in_a_tab_of_its_own_and_the_callers_tab_is_not_moved(tmp_path):
    ops, world, watcher = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    out = run(ops, "browser.download", url=PLAT_URL)
    assert out.ok and out.data["verified"] is True and out.data["files"][0]["name"] == "0337201.tif"
    assert world.running["chrome"][0].url == "https://a.example/x?token=SECRET", "the caller's tab moved"
    assert world.page.tab.id == "chrome-2" and world.page.current_url == PLAT_URL
    assert world.closed == ["chrome-2"] and world.page.released
    assert world.download_dirs == [watcher.chrome_dir, FakeFolder().directory]
    assert "SECRET" not in out.line and out.data["url"].startswith("https://plats.example/files/0337201.tif")


def test_a_navigation_chrome_aborts_because_it_became_a_download_still_counts(tmp_path):
    from browser_workflows.errors import BrowserWorkflowError
    ops, world, _ = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    original = world.attach

    def attach(kind, tab_id):
        page = original(kind, tab_id)
        def aborted(url):
            raise BrowserWorkflowError(f"could not open {url}: net::ERR_ABORTED")
        page.navigate = aborted
        return page
    world.attach = attach
    out = run(ops, "browser.download", url=PLAT_URL)
    assert out.ok and out.data["verified"] is True


def test_an_address_showing_a_pdf_saves_what_the_viewer_shows(tmp_path):
    pdf = a_file(tmp_path, "fw9.pdf", b"%PDF-1.7 rest")
    ops, world, watcher = wired(tmp_path, running=("chrome",))
    arrivals = iter([[], [pdf]])
    watcher.wait_new = lambda before, timeout_seconds=30.0: next(arrivals)
    original = world.attach

    def attach(kind, tab_id):
        page = original(kind, tab_id)
        page.save_shown_file = lambda: True
        return page
    world.attach = attach
    out = run(ops, "browser.download", url="https://irs.example/pub/fw9.pdf")
    assert out.ok and out.data["files"][0]["mime"] == "application/pdf"


def test_a_sign_in_page_is_left_open_for_eg_and_never_called_the_file(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",))
    world.elements = [FakeElement(text="", attrs={"type": "password"})]
    world.elements[0].matches = lambda css: "password" in css
    out = run(ops, "browser.download", url=PLAT_URL)
    assert not out.ok and out.data["cause"] == "sign_in_required" and out.data["needs_person"] is True
    assert world.closed == [] and world.active == "chrome-2" and out.data["tab"] == "chrome-2"
    assert "did not sign in" in out.line and "SECRET" not in out.line


def test_an_ordinary_web_page_is_not_a_file_and_its_tab_is_closed(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",))
    world.elements = []
    out = run(ops, "browser.download", url="https://plats.example/search")
    assert not out.ok and out.data["cause"] == "not_a_file" and world.closed == ["chrome-2"]


def test_a_fetch_chrome_could_not_make_says_why(tmp_path):
    from browser_workflows.errors import BrowserWorkflowError
    ops, world, _ = wired(tmp_path, running=("chrome",))
    world.elements = []
    original = world.attach

    def attach(kind, tab_id):
        page = original(kind, tab_id)
        def unresolved(url):
            raise BrowserWorkflowError(f"could not open {url}: net::ERR_NAME_NOT_RESOLVED")
        page.navigate = unresolved
        return page
    world.attach = attach
    out = run(ops, "browser.download", url=PLAT_URL)
    assert not out.ok and out.data["cause"] == "fetch_failed" and "ERR_NAME_NOT_RESOLVED" in out.line
    assert "SECRET" not in out.line


def test_only_http_addresses_are_asked_for(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",))
    for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "chrome://settings"):
        out = run(ops, "browser.download", url=bad)
        assert not out.ok and "http or https" in out.line
    assert len(world.running["chrome"]) == 1, "no tab may open for a refused address"


def test_an_address_and_an_element_together_are_refused(tmp_path):
    ops, world, _ = wired(tmp_path, running=("chrome",))
    out = run(ops, "browser.download", url=PLAT_URL, text="0337201.tif")
    assert not out.ok and "not both" in out.line and len(world.running["chrome"]) == 1


def test_a_consequential_address_needs_confirmation(tmp_path):
    ops, world, _ = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    out = run(ops, "browser.download", url="https://bank.example/pay-now/statement.pdf")
    assert not out.ok and out.data.get("needs_confirmation") is True and len(world.running["chrome"]) == 1

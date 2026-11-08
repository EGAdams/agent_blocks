"""browser.download over fake interfaces: a file that lands, a file that does not, and a file that is wrong.

The one rule under test: a press that saved nothing is never reported as a download.

    cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests/test_downloads.py
"""
from __future__ import annotations

from pathlib import Path

from browser_control.filelinks import saved_extension
from .test_operations import FakeElement, make, run


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
    """IDownloadWatcher: hands back whatever files the test said would arrive."""

    def __init__(self, arriving=()):
        self.arriving, self.waited = list(arriving), None

    def before(self):
        return set()

    def wait_new(self, before, timeout_seconds=30.0):
        self.waited = timeout_seconds
        return list(self.arriving)


class FakeVerifier:
    def __init__(self, problems=None):
        self.problems = problems or {}

    def problem(self, path):
        return self.problems.get(path.name, "")


def wired(tmp_path, arriving=(), problems=None, folder=None, **world_args):
    folder = folder or FakeFolder()
    watcher = FakeWatcher(arriving)
    ops, world = make(tmp_path, downloads=(folder, watcher, FakeVerifier(problems)), **world_args)
    world.elements = [FakeElement(text="0337201.tif", attrs={"href": "/docs/0337201.tif"})]
    return ops, world, watcher


def a_file(tmp_path, name="0337201.tif", body=b"II*\x00rest-of-a-tiff"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


# -- what lands -------------------------------------------------------------------


def test_a_file_that_arrives_is_reported_with_its_name_and_size(tmp_path):
    saved = a_file(tmp_path)
    ops, world, watcher = wired(tmp_path, arriving=[saved], running=("chrome",))
    out = run(ops, "browser.download", text="0337201.tif")
    assert out.ok and out.data["verified"] is True
    assert out.data["files"][0]["name"] == "0337201.tif"
    assert out.data["files"][0]["bytes"] == saved.stat().st_size
    assert out.data["files"][0]["head"] == b"II*\x00rest".hex()[:16]
    assert "0337201.tif" in out.line and world.download_dir == FakeFolder().directory


def test_chrome_is_told_where_to_save_before_the_press(tmp_path):
    ops, world, _ = wired(tmp_path, arriving=[a_file(tmp_path)], running=("chrome",))
    run(ops, "browser.download", text="0337201.tif")
    #: The whole bug: without this call Chrome writes nothing and the click still looks successful.
    assert world.download_dir == FakeFolder().directory


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

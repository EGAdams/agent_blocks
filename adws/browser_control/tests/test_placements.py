"""RememberedPlacement: Windows is asked once per Chrome browser process, never trusted across a restart."""
from __future__ import annotations

from browser_control.models import BROWSERS, BrowserPlacement
from browser_control.placements import RememberedPlacement

CHROME = BROWSERS["chrome"]
SEEN = BrowserPlacement(known=True, found=True, pid=16328, session=1, desktops=[1])
STALL = BrowserPlacement(known=False, reason="Windows did not say where chrome is (UtilAcceptVsock ... 110)")


class Windows:
    def __init__(self, *answers): self.answers, self.asked = list(answers), 0
    def placement(self, kind): self.asked += 1; return self.answers.pop(0)


class Chrome:
    def __init__(self, pid): self.current = pid
    def pid(self, kind): return self.current


def test_a_stall_after_one_good_answer_is_answered_from_memory(tmp_path):
    windows, chrome = Windows(SEEN, STALL), Chrome(16328)
    remembered = RememberedPlacement(windows, chrome, tmp_path / "placements.json")
    assert remembered.placement(CHROME).visible and windows.asked == 1
    again = RememberedPlacement(windows, chrome, tmp_path / "placements.json")   # a later ADW run, same file
    assert again.placement(CHROME) == SEEN and windows.asked == 1, "the same Chrome is not asked about twice"


def test_a_restarted_chrome_is_asked_about_afresh(tmp_path):
    hidden = BrowserPlacement(known=True, found=True, pid=20000, session=0, desktops=[1])
    windows, chrome = Windows(SEEN, hidden), Chrome(16328)
    remembered = RememberedPlacement(windows, chrome, tmp_path / "placements.json")
    remembered.placement(CHROME)
    chrome.current = 20000
    out = remembered.placement(CHROME)
    assert windows.asked == 2 and not out.visible, "a hidden new Chrome never inherits the old one's answer"


def test_nothing_is_remembered_without_a_pid_or_from_a_failed_or_mismatched_answer(tmp_path):
    memory = tmp_path / "placements.json"
    RememberedPlacement(Windows(SEEN), Chrome(0), memory).placement(CHROME)
    RememberedPlacement(Windows(STALL), Chrome(16328), memory).placement(CHROME)
    RememberedPlacement(Windows(SEEN), Chrome(99), memory).placement(CHROME)
    assert not memory.exists()
    windows = Windows(STALL)
    assert RememberedPlacement(windows, Chrome(16328), memory).placement(CHROME) == STALL and windows.asked == 1


def test_an_unreadable_memory_is_forgotten_not_trusted(tmp_path):
    memory = tmp_path / "placements.json"
    memory.write_text('{"9222": {"known": "maybe", "pid": "x"}}')
    windows = Windows(SEEN)
    assert RememberedPlacement(windows, Chrome(16328), memory).placement(CHROME) == SEEN and windows.asked == 1
    memory.write_text("not json")
    assert RememberedPlacement(Windows(SEEN), Chrome(16328), memory).placement(CHROME) == SEEN

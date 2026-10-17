"""The file demonstration's three steps, as plain code with no menu and no SSSF.

Open -> read -> print exists to make the phase boundary visible: three pieces of
work that an ADW would do in one breath, separated so an engineer can run one,
look at it, and decide. The steps know nothing about phases, tracing, or menus —
`debug_console.py` wraps them in a phase and `adw_debug_console.py` draws them.

DemoSession is a plain object, not a BaseModel: it owns a live file handle, so
it is a resource, not a wire contract. `DemoStateView` is its serializable
snapshot, and that is the thing allowed to cross a boundary.
"""

from __future__ import annotations

from pathlib import Path

from .data_types import DemoStateView
from .utils import now_iso

MAX_BYTES = 1_000_000           # the demo reads whole files; refuse to eat a GB
PREVIEW_LINES = 40              # step 3 prints at most this many lines


class StepNotReady(RuntimeError):
    """A step was asked for before the step it depends on. Nothing was done."""


class DemoSession:
    """The state that carries between menu selections."""

    def __init__(self) -> None:
        self.selected_path: str = ""
        self.handle = None
        self.opened_at: str = ""
        self.read_at: str = ""
        self.text: str | None = None
        self.byte_count: int | None = None
        self.line_count: int | None = None
        self.phases_run: list[str] = []

    # ── step 1: open ────────────────────────────────────────────────────────
    def open_file(self, path: str) -> dict:
        """Acquire the file handle. Deliberately reads nothing.

        Opening a DIFFERENT file (or the same one again) drops whatever step 2
        had read. Stale text surviving a re-open is exactly the bug this
        demonstration is meant to make impossible to miss.

        Every validation runs BEFORE the old handle is released, so a rejected
        path is a no-op: the previously open file stays open and readable. A
        failed selection that quietly destroyed working state would be the
        worse surprise of the two.
        """
        target = Path(path).expanduser()
        if not target.exists():
            raise StepNotReady(f"no such file: {target}")
        if target.is_dir():
            raise StepNotReady(f"{target} is a directory, not a file")
        size = target.stat().st_size
        if size > MAX_BYTES:
            raise StepNotReady(f"{target} is {size}B, over the {MAX_BYTES}B demo limit")

        self.release()                      # a second open never leaks the first
        self.forget_contents()
        self.handle = target.open("r", encoding="utf-8", errors="strict")
        self.selected_path = str(target)
        self.opened_at = now_iso()
        return {"path": self.selected_path, "size_on_disk": size}

    # ── step 2: read ────────────────────────────────────────────────────────
    def read_file(self) -> dict:
        """Pull the text into memory. Deliberately displays nothing."""
        if self.handle is None:
            raise StepNotReady("no file is open — run step 1 (open) first")
        self.handle.seek(0)                 # a re-read starts from the top
        text = self.handle.read()
        self.text = text
        self.byte_count = len(text.encode("utf-8"))
        self.line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        self.read_at = now_iso()
        return {"bytes": self.byte_count, "lines": self.line_count,
                "characters": len(text)}

    # ── step 3: print ───────────────────────────────────────────────────────
    def contents_to_print(self) -> dict:
        """Hand back what step 2 already read. Never re-reads the file."""
        if self.text is None:
            raise StepNotReady("nothing has been read — run step 2 (read) first")
        lines = self.text.splitlines()
        return {"lines": lines[:PREVIEW_LINES],
                "shown": min(len(lines), PREVIEW_LINES),
                "total": len(lines),
                "read_at": self.read_at}

    # ── resources ───────────────────────────────────────────────────────────
    def forget_contents(self) -> None:
        self.text = None
        self.byte_count = None
        self.line_count = None
        self.read_at = ""

    def release(self) -> bool:
        """Close the handle if one is open. Safe to call twice; reset and exit
        both route through here, so neither can leak a descriptor."""
        if self.handle is None:
            return False
        try:
            self.handle.close()
        finally:
            self.handle = None
        return True

    def clear(self) -> bool:
        """Drop the whole demonstration: handle, selection and contents.

        Keeps the same object (and so the same `phases_run` list), because the
        menu's "clear this session's state" stays inside one adw_id — the trace
        of what already ran is history, not state.
        """
        released = self.release()
        self.forget_contents()
        self.selected_path = ""
        self.opened_at = ""
        return released

    def view(self, adw_id: str = "") -> DemoStateView:
        return DemoStateView(
            adw_id=adw_id,
            selected_path=self.selected_path,
            handle_open=self.handle is not None,
            opened_at=self.opened_at,
            read_at=self.read_at,
            byte_count=self.byte_count,
            line_count=self.line_count,
            has_text=self.text is not None,
            phases_run=list(self.phases_run),
        )

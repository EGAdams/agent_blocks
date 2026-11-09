"""Reading the dashboard page: a remote Proxy over the browser.notes_state ADW, and a Null Object.

`AdwPageReader` never touches Chrome itself. It runs `adw_browser_control.py browser.notes_state` -- its own ADW,
with its own id and lifecycle log -- and turns the answer into a `PageReading`, keeping the page's ADW id so the
caller can show both runs. `NoPage` answers "not checked" for a caller that did not ask for the page.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .models import PageReading

ADW_ID = re.compile(r"adw_id: ([0-9a-f]{8})")


class AdwPageReader:
    def __init__(self, uv: str, adw: Path, cwd: Path, timeout_seconds: float = 150.0, run=subprocess.run) -> None:
        self._uv, self._adw, self._cwd = uv, Path(adw), Path(cwd)
        self._timeout = timeout_seconds
        self._run = run

    def read(self, note: str, reload: bool) -> PageReading:
        with tempfile.TemporaryDirectory(prefix="notes_page_") as folder:
            out = Path(folder) / "page.json"
            argv = [self._uv, "run", "--quiet", str(self._adw), "browser.notes_state", f"--note={note}",
                    f"--json-out={out}"] + (["--reload"] if reload else [])
            try:
                done = self._run(argv, cwd=str(self._cwd), capture_output=True, text=True, timeout=self._timeout)
            except subprocess.TimeoutExpired:
                return PageReading(ok=False, line="The page check took too long.", cause="page_timeout")
            found = ADW_ID.search((done.stdout or "") + (done.stderr or ""))
            adw_id = found.group(1) if found else ""
            if not out.exists():
                tail = ((done.stderr or done.stdout or "").strip().splitlines() or ["no output"])[-1]
                return PageReading(ok=False, line=f"The page check gave no answer ({tail[:200]}).",
                                   adw_id=adw_id, cause="page_no_answer")
            answer = json.loads(out.read_text(encoding="utf-8"))
        data = answer.get("data") or {}
        rows = data.get("highlights") or []
        lit = [r for r in rows if r.get("blinking")]
        return PageReading(ok=bool(answer.get("ok")), line=str(answer.get("line") or ""), adw_id=adw_id,
                           revision=data.get("revision"), blinking=[str(t) for t in data.get("blinking") or []],
                           text_visible=all(r.get("text_visible", True) for r in lit), highlighted=len(rows),
                           reloaded=bool(data.get("reloaded")), cause=str(data.get("cause") or ""), raw=data)


class NoPage:
    def read(self, note: str, reload: bool) -> PageReading:
        return PageReading(ok=False, line="The page was not checked.", cause="not_checked")

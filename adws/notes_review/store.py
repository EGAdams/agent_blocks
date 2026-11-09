"""BridgeNoteStore: Adapter from INoteStore to the dashboard's token-protected Notes interface.

The dashboard owns the notes and checks revisions itself (a write over a stale revision answers 409); this adapter
only translates. It is the same interface the Notes agent uses, reached on loopback with the same 0600 token file,
so an edit made here is the edit the page shows and the one every other Notes caller sees.
"""
from __future__ import annotations

import os
from pathlib import Path

from .interfaces import NoteMoved

DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_TOKEN = Path.home() / ".config" / "startrek_notes" / "notes_token"


class BridgeNoteStore:
    """`bridge` is the HttpNotesBridge, or a callable that builds it on first use -- so a missing token file is an
    OSError inside the operation (one honest refusal), not a traceback while the ADW is still starting."""

    def __init__(self, bridge, task_id: str = "notes-review") -> None:
        self._bridge = bridge
        self._task = task_id

    @property
    def bridge(self):
        if callable(self._bridge) and not hasattr(self._bridge, "read"):
            self._bridge = self._bridge()
        return self._bridge

    def read(self, note: str) -> tuple[str, str]:
        return self.bridge.read(note, self._task)

    def write(self, note: str, text: str, expected_revision: str) -> str:
        from agent_bridge_worker.adapters.notes_bridge_http import NoteChanged
        try:
            return self.bridge.write(note, text, expected_revision, self._task)
        except NoteChanged as exc:
            raise NoteMoved(str(exc)) from None


def bridge_from_environment(task_id: str = "notes-review") -> BridgeNoteStore:
    """The live store: URL from NOTES_ENDPOINT_URL, token from NOTES_TOKEN_FILE (defaults as the worker's)."""
    from agent_bridge_worker.adapters.http_scan_facade import load_scan_token
    from agent_bridge_worker.adapters.notes_bridge_http import HttpNotesBridge
    url = os.environ.get("NOTES_ENDPOINT_URL", DEFAULT_URL)
    token_file = Path(os.environ.get("NOTES_TOKEN_FILE", str(DEFAULT_TOKEN)))

    def connect():
        if not token_file.exists():          # not FileNotFoundError: that would read as "no such note"
            raise RuntimeError(f"the Notes token is not provisioned on this machine ({token_file})")
        return HttpNotesBridge(url, load_scan_token(token_file))
    return BridgeNoteStore(connect, task_id)

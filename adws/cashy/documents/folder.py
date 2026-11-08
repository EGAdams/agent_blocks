"""Cashy's name for the shared download machinery, which now lives a layer down.

`FolderDownloadWatcher`, `FileDownloadVerifier`, `local_path` and `sha256` were written here for
Cashy's statements, but they know nothing about Cashy -- only about a folder Chrome writes into. They
moved to `browser_workflows/playback/downloads.py` when Browser Control's `browser.download` needed
the same waiting and checking, because `browser_control` importing `cashy` would have pointed the
dependency the wrong way (an agent is built on the machinery, never the machinery on the agent).
Cashy's own imports are unchanged.
"""
from __future__ import annotations

from browser_workflows.playback.downloads import (PARTIAL, FileDownloadVerifier, FolderDownloadWatcher,
                                                  local_path, sha256)

__all__ = [ "PARTIAL", "FileDownloadVerifier", "FolderDownloadWatcher", "local_path", "sha256" ]

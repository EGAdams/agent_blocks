"""The temp-file retention rule: page folders older than the limit are removed.

`WindowsSpoolerBackend` deletes its page images as soon as the spooler call
returns, so anything left under the spool root is from a run that died
mid-submit. `sweep` removes such leftovers once they are older than the
retention limit, and only direct children of the root that look like job
folders -- never anything it did not make.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .models import JOB_ID


def sweep( root: Path, max_age_seconds: float, now: float ) -> list[ str ]:
    """Remove job folders under `root` last changed more than `max_age_seconds` before `now`. Returns their names."""
    if not root.is_dir():
        return []
    removed: list[ str ] = []
    for child in sorted( root.iterdir()):
        if child.is_symlink() or not child.is_dir() or not JOB_ID.fullmatch( child.name ):
            continue
        if now - child.stat().st_mtime > max_age_seconds:
            shutil.rmtree( child, ignore_errors=True )
            removed.append( child.name )
    return removed

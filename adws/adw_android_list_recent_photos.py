#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic", "pillow", "pillow-heif"]
# ///
"""ADW Android List Recent Photos -- the newest N camera photos (default 15), newest first, as thumbnails.
One operation of `adw_android_photos.py`; see `android_photos/DESIGN.md`."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from android_photos.adw import run                                        # noqa: E402

if __name__ == "__main__":
    sys.exit(run("list_recent_photos"))

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic", "pillow", "pillow-heif"]
# ///
"""ADW Android Get Photo -- one photograph at viewing size, by --number (from the last listing) or --name.
One operation of `adw_android_photos.py`; see `android_photos/DESIGN.md`."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from android_photos.adw import run                                        # noqa: E402

if __name__ == "__main__":
    sys.exit(run("get_photo"))

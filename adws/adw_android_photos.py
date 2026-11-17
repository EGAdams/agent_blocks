#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic", "pillow", "pillow-heif"]
# ///
"""ADW Android Photos -- read-only access to the camera photos on EG's USB-connected Android phone.

    adw_android_photos.py status
    adw_android_photos.py list_recent_photos --count 15
    adw_android_photos.py get_thumbnail --number 3
    adw_android_photos.py get_photo --number 3          (or --name IMG_20261114_093015.jpg)

Read `android_photos/DESIGN.md` first. The phone is only ever read. Nothing is copied off it but the photos asked
for, into a private cache that empties itself after a day. This is what the Bridge's `android-photos` agent runs.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from android_photos.adw import run                                        # noqa: E402

if __name__ == "__main__":
    sys.exit(run())

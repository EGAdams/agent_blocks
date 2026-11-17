"""When was this picture taken? Three sources, best first:

    exif      DateTimeOriginal inside the file -- what the camera wrote
    filename  the timestamp Android cameras put in the name (IMG_20261114_093015.jpg, PXL_20261114_143015123.jpg)
    modified  the date the phone reports for the file

The phone can only be asked for names and modified dates without copying a file, so candidates are ranked by
filename, then modified; EXIF is read from the few that were copied, and they are sorted again by it.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .models import RemotePhoto

_STAMP = re.compile(r"(?<!\d)(20\d{2})[-_.]?([01]\d)[-_.]?([0-3]\d)[-_ T.]?([0-2]\d)[-_.]?([0-5]\d)[-_.]?([0-5]\d)")
ISO = "%Y-%m-%dT%H:%M:%S"


def from_filename(name: str) -> str:
    match = _STAMP.search(name)
    if not match:
        return ""
    try:
        return datetime(*(int(part) for part in match.groups())).strftime(ISO)
    except ValueError:
        return ""


def best_before_copy(photo: RemotePhoto) -> tuple[str, str]:
    """(when, source) from what the phone lists."""
    stamp = from_filename(photo.name)
    if stamp:
        return stamp, "filename"
    return (photo.modified, "modified") if photo.modified else ("", "unknown")


def newest_first(photos: list[RemotePhoto]) -> list[RemotePhoto]:
    """Newest first; a file with no date at all goes last; the name breaks a tie so the order is stable."""
    return sorted(photos, key=lambda p: (best_before_copy(p)[0], p.name), reverse=True)


class ExifCaptureClock:
    """ICaptureClock over Pillow. Any file it cannot read simply has no EXIF date."""

    def taken_at(self, original: Path) -> str:
        try:
            from PIL import Image
            with Image.open(original) as image:
                exif = image.getexif()
                raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)      # DateTimeOriginal, else DateTime
            return datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").strftime(ISO) if raw else ""
        except Exception:                                                        # noqa: BLE001 - an unreadable file has no date
            return ""

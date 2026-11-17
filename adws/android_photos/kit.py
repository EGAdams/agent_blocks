"""Android Photos' only composition root: the one module that names the classes behind the interfaces.

Two environment variables move it, for tests and for a trial beside the real one:
    ANDROID_PHOTOS_ROOT      the private cache            (default ~/.local/share/android_photos)
    ANDROID_PHOTOS_SETTINGS  the allowlist                (default ~/.config/android_photos/settings.json)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from browser_control.link import WslInteropLink

from .cache import FileLastListing, PhotoCache
from .capture_time import ExifCaptureClock
from .interfaces import IPhoneReader
from .models import PhotoSettings
from .operations import Fetcher, OnePhoto, PhoneGate, RecentPhotos
from .powershell_reader import PowerShellPhoneReader
from .rendering import PillowRenderer


def settings_path() -> Path:
    return Path(os.environ.get("ANDROID_PHOTOS_SETTINGS") or Path.home() / ".config/android_photos/settings.json")


def load_settings(path: Path) -> PhotoSettings:
    """No file, or one that cannot be read, authorizes nobody."""
    try:
        return PhotoSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return PhotoSettings()


class Kit:
    def __init__(self, reader: IPhoneReader | None = None) -> None:
        root = Path(os.environ.get("ANDROID_PHOTOS_ROOT") or Path.home() / ".local/share/android_photos")
        self.settings_file = settings_path()
        self.settings = load_settings(self.settings_file)
        self.reader = reader or PowerShellPhoneReader(WslInteropLink())
        self.cache = PhotoCache(root)
        self.last = FileLastListing(root / "last_list.json")
        self.gate = PhoneGate(self.reader, self.settings)
        fetcher = Fetcher(self.reader, self.cache, PillowRenderer(), ExifCaptureClock())
        self.recent = RecentPhotos(self.gate, fetcher, self.last)
        self.one = OnePhoto(self.gate, fetcher, self.last)

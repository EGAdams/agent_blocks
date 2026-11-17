"""The data that crosses a boundary in Android Photos. Nothing here knows about Windows, MTP or the Bridge."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_FOLDERS = ["DCIM/Camera", "DCIM/100ANDRO", "DCIM/100MEDIA", "DCIM/OpenCamera"]
#: What counts as a photograph. Video and raw files are left on the phone.
PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp"})
DEFAULT_COUNT = 15
MAX_COUNT = 20                       # the gateway keeps at most 20 files per task


class PhotoSettings(BaseModel):
    """`~/.config/android_photos/settings.json`. Only a device named here is ever read."""
    model_config = ConfigDict(extra="ignore", frozen=True)

    devices: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=lambda: list(DEFAULT_FOLDERS))


class DeviceSeen(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    storages: list[str] = Field(default_factory=list)


class RemotePhoto(BaseModel):
    """One file as the phone lists it. `modified` is the phone's local time, or "" when it gave none."""
    model_config = ConfigDict(frozen=True)

    storage: str
    folder: str
    name: str
    modified: str = ""

    @property
    def key(self) -> str:
        return f"{self.storage}/{self.folder}/{self.name}"


class Listing(BaseModel):
    folders: list[str] = Field(default_factory=list)
    photos: list[RemotePhoto] = Field(default_factory=list)


class ReaderProblem(Exception):
    """The phone could not be read. `cause` is one of: no_device, locked, reader_failed, no_windows."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


class Rendition(BaseModel):
    """One display-ready JPEG. `rendition` is the gateway's word: preview (thumbnail) or full."""
    rendition: str
    path: Path
    mime: str = "image/jpeg"
    width: int
    height: int
    bytes: int
    sha256: str


class Photo(BaseModel):
    """One photograph in an answer. `number` is its place in the last listing, 1 = newest."""
    number: int
    name: str
    folder: str
    storage: str
    modified: str = ""
    taken_at: str = ""
    taken_source: str = ""           # exif | filename | modified | unknown
    bytes: int = 0
    original: Path | None = None     # the copy in this machine's private cache


class PhoneStatus(BaseModel):
    state: str                       # ready | no_device | locked | not_authorized | no_camera_folder | reader_failed | no_windows
    line: str
    device: str = ""
    devices_seen: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)
    photo_count: int = 0
    next_step: str = ""


class PhotoAnswer(BaseModel):
    """What a photo operation answers. `files` is what the Bridge worker publishes so the pictures can be shown."""
    state: str                       # ready | not_found | unreadable
    line: str
    ok: bool = True
    device: str = ""
    photos: list[dict] = Field(default_factory=list)
    files: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    total_on_phone: int = 0
    next_step: str = ""

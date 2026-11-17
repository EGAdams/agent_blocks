"""The interfaces Android Photos is written against. `kit.py` is the only module that names what satisfies them."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import DeviceSeen, Listing, RemotePhoto, Rendition


class IPhoneReader(Protocol):
    """Read-only by contract: there is no method here that could change the phone."""

    def look(self) -> list[DeviceSeen]: ...
    def list(self, device: str, folders: list[str]) -> Listing: ...
    def copy(self, device: str, photos: list[RemotePhoto], into: dict[str, Path]) -> dict[str, Path]:
        """Copies each photo off the phone into the folder `into` names for its key; returns key -> the copy.
        A photo that could not be copied is absent from the answer."""
        ...


class IPhotoRenderer(Protocol):
    def render(self, original: Path, rendition: str, out_dir: Path) -> Rendition: ...


class ICaptureClock(Protocol):
    """When a photograph was taken, read from the file itself. "" when the file does not say."""

    def taken_at(self, original: Path) -> str: ...


class ILastListing(Protocol):
    """Remembers the last listing, so "picture number three" means the third one EG was shown."""

    def save(self, device: str, photos: list[dict]) -> None: ...
    def load(self) -> tuple[str, list[dict]]: ...

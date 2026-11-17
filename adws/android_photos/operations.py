"""The three things Android Photos does, each written against the interfaces only.

    PhoneGate      is the phone here, authorized, unlocked, and does it have a camera folder?
    RecentPhotos   the newest N photographs, as thumbnails
    OnePhoto       one photograph, by its number in the last listing or by name

Every one of them only reads. A phone that is absent, locked or not on the allowlist is answered with what EG
has to do, never worked around.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .cache import PhotoCache
from .capture_time import best_before_copy, newest_first
from .interfaces import ICaptureClock, ILastListing, IPhoneReader, IPhotoRenderer
from .models import (DEFAULT_COUNT, MAX_COUNT, Listing, PhoneStatus, Photo, PhotoAnswer, PhotoSettings, ReaderProblem,
                     RemotePhoto, Rendition)
from .rendering import RenderFailed

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic", ".heif": "image/heif",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
UNLOCK = "Unlock the phone, then on the phone's USB notification choose \"File transfer\"."
PLUG_IN = "Plug the phone into the Windows computer with a USB data cable, unlock it, and choose \"File transfer\"."


class NotReady(Exception):
    def __init__(self, status: PhoneStatus) -> None:
        super().__init__(status.line)
        self.status = status


class PhoneGate:
    def __init__(self, reader: IPhoneReader, settings: PhotoSettings) -> None:
        self._reader, self._settings = reader, settings

    def device(self) -> str:
        """The one authorized, unlocked phone that is attached. Raises NotReady with what to do otherwise."""
        try:
            seen = self._reader.look()
        except ReaderProblem as problem:
            raise NotReady(self._problem(problem)) from None
        names = [d.name for d in seen]
        if not seen:
            raise NotReady(PhoneStatus(state="no_device", line="No phone is attached to this computer.", next_step=PLUG_IN))
        allowed = [d for d in seen if d.name in self._settings.devices]
        if not allowed:
            raise NotReady(PhoneStatus(
                state="not_authorized", devices_seen=names,
                line=f"A phone is attached ({', '.join(names)}), but it is not authorized for photo access.",
                next_step="EG authorizes it once, on this computer: adw_android_authorize.py --device \"<its name>\"."))
        ready = [d for d in allowed if d.storages]
        if not ready:
            raise NotReady(PhoneStatus(state="locked", device=allowed[0].name, devices_seen=names,
                                       line=f"{allowed[0].name} is attached but shows no storage: it is locked, or "
                                            "not in File transfer mode.", next_step=UNLOCK))
        return ready[0].name

    def open(self) -> tuple[str, Listing]:
        device = self.device()
        try:
            listing = self._reader.list(device, list(self._settings.folders))
        except ReaderProblem as problem:
            raise NotReady(self._problem(problem, device)) from None
        if not listing.folders:
            raise NotReady(PhoneStatus(state="no_camera_folder", device=device,
                                       line=f"{device} has none of the permitted camera folders "
                                            f"({', '.join(self._settings.folders)}).",
                                       next_step="Add the phone's camera folder to settings.json's \"folders\"."))
        return device, listing

    def status(self) -> PhoneStatus:
        try:
            device, listing = self.open()
        except NotReady as not_ready:
            return not_ready.status
        return PhoneStatus(state="ready", device=device, folders=listing.folders, photo_count=len(listing.photos),
                           line=f"{device} is connected and readable: {len(listing.photos)} photo(s) in "
                                f"{', '.join(listing.folders)}. Access is read-only.")

    @staticmethod
    def _problem(problem: ReaderProblem, device: str = "") -> PhoneStatus:
        if problem.cause == "locked":
            return PhoneStatus(state="locked", device=device, line=f"{device or 'The phone'} shows no storage: it is "
                               "locked, or not in File transfer mode.", next_step=UNLOCK)
        if problem.cause == "no_device":
            return PhoneStatus(state="no_device", line="The phone was disconnected.", next_step=PLUG_IN)
        state = "no_windows" if problem.cause == "no_windows" else "reader_failed"
        return PhoneStatus(state=state, device=device, line=f"The phone could not be read: {problem}",
                           next_step="Try again; if it repeats, unplug the phone and plug it back in.")


class Fetcher:
    """Brings requested photographs into the cache (once each) and makes their display copies."""

    def __init__(self, reader: IPhoneReader, cache: PhotoCache, renderer: IPhotoRenderer, clock: ICaptureClock) -> None:
        self._reader, self._cache, self._renderer, self._clock = reader, cache, renderer, clock

    def originals(self, device: str, photos: list[RemotePhoto]) -> dict[str, Path]:
        have = {p.key: path for p in photos if (path := self._cache.original(device, p))}
        missing = [p for p in photos if p.key not in have]
        if missing:
            have.update(self._reader.copy(device, missing, {p.key: self._cache.original_dir(device, p) for p in missing}))
        return have

    def described(self, remote: RemotePhoto, original: Path) -> Photo:
        when, source = self._clock.taken_at(original), "exif"
        if not when:
            when, source = best_before_copy(remote)
        return Photo(number=0, name=remote.name, folder=remote.folder, storage=remote.storage, modified=remote.modified,
                     taken_at=when, taken_source=source, bytes=original.stat().st_size, original=original)

    def render(self, device: str, remote: RemotePhoto, original: Path, rendition: str) -> Rendition:
        return self._renderer.render(original, rendition, self._cache.rendition_dir(device, remote))


def page(rendition: Rendition) -> dict:
    return {"page": 1, "rendition": rendition.rendition, "path": str(rendition.path), "mime": rendition.mime,
            "width": rendition.width, "height": rendition.height, "bytes": rendition.bytes, "sha256": rendition.sha256}


def thumbnail_file(photo: Photo, preview: Rendition) -> dict:
    """The file handed to the gateway IS the thumbnail: for a listing the original never leaves this machine."""
    return {"name": photo.name, "original_name": photo.name, "path": str(preview.path), "mime": preview.mime,
            "bytes": preview.bytes, "sha256": preview.sha256, "number": photo.number,
            "inspection": {"kind": "image", "page_count": 1, "text": "", "pages": [page(preview)],
                           "note": f"thumbnail of photo {photo.number}, taken {photo.taken_at or 'at an unknown time'}"}}


def original_file(photo: Photo, renditions: list[Rendition]) -> dict:
    data = photo.original.read_bytes()
    return {"name": photo.name, "original_name": photo.name, "path": str(photo.original),
            "mime": MIME.get(photo.original.suffix.lower(), "application/octet-stream"), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "number": photo.number,
            "inspection": {"kind": "image", "page_count": 1, "text": "", "pages": [page(r) for r in renditions],
                           "note": f"photo {photo.number}, taken {photo.taken_at or 'at an unknown time'}"}}


def public(photo: Photo) -> dict:
    return photo.model_dump(mode="json", exclude={"original"})


class RecentPhotos:
    def __init__(self, gate: PhoneGate, fetcher: Fetcher, last: ILastListing) -> None:
        self._gate, self._fetcher, self._last = gate, fetcher, last

    def run(self, count: int = DEFAULT_COUNT) -> PhotoAnswer:
        count = max(1, min(int(count), MAX_COUNT))
        device, listing = self._gate.open()
        wanted = newest_first(listing.photos)[:count]
        try:
            originals = self._fetcher.originals(device, wanted)
        except ReaderProblem as problem:
            raise NotReady(PhoneGate._problem(problem, device)) from None
        found = [(remote, self._fetcher.described(remote, originals[remote.key])) for remote in wanted
                 if remote.key in originals]
        # Sorted again now that the files can say when they were taken; the order they came in breaks a tie.
        found.sort(key=lambda pair: pair[1].taken_at, reverse=True)
        photos, files, notes = [], [], []
        for number, (remote, photo) in enumerate(found, start=1):
            photo.number = number
            photos.append(photo)
            try:
                files.append(thumbnail_file(photo, self._fetcher.render(device, remote, photo.original, "preview")))
            except RenderFailed as failed:
                notes.append(str(failed))
        missed = len(wanted) - len(found)
        if missed:
            notes.append(f"{missed} photo(s) could not be copied off the phone")
        self._last.save(device, [public(p) for p in photos])
        line = (f"The {len(photos)} newest photo(s) on {device}, newest first." if photos
                else f"{device} has no photos in {', '.join(listing.folders)}.")
        return PhotoAnswer(state="ready", device=device, line=line, photos=[public(p) for p in photos], files=files,
                           notes=notes, total_on_phone=len(listing.photos))


class OnePhoto:
    """`rendition` full = the photograph (original plus a viewing copy); preview = its thumbnail only."""

    def __init__(self, gate: PhoneGate, fetcher: Fetcher, last: ILastListing) -> None:
        self._gate, self._fetcher, self._last = gate, fetcher, last

    def run(self, rendition: str, number: int | None = None, name: str | None = None) -> PhotoAnswer:
        device = self._gate.device()
        listed_on, listed = self._last.load()
        entry = None
        if listed_on == device:
            entry = next((p for p in listed if (number is not None and p.get("number") == number)
                          or (name and str(p.get("name", "")).lower() == name.lower())), None)
        if entry is None and name:
            _, listing = self._gate.open()
            match = next((p for p in listing.photos if p.name.lower() == name.lower()), None)
            entry = {**match.model_dump(), "number": 0} if match else None
        if entry is None:
            what = f"number {number}" if number is not None else f"called {name}"
            return PhotoAnswer(state="not_found", device=device, ok=False,
                               line=f"There is no photo {what} in the last listing.",
                               next_step="Ask for the recent photos first, then for one of them by its number.")
        remote = RemotePhoto(storage=entry["storage"], folder=entry["folder"], name=entry["name"],
                             modified=entry.get("modified") or "")
        try:
            original = self._fetcher.originals(device, [remote]).get(remote.key)
        except ReaderProblem as problem:
            raise NotReady(PhoneGate._problem(problem, device)) from None
        if original is None:
            return PhotoAnswer(state="not_found", device=device, ok=False,
                               line=f"{remote.name} could not be copied off the phone; it may have been deleted.",
                               next_step="Refresh the list of recent photos.")
        photo = self._fetcher.described(remote, original)
        photo.number = int(entry.get("number") or 0)
        try:
            preview = self._fetcher.render(device, remote, original, "preview")
            if rendition == "preview":
                files = [thumbnail_file(photo, preview)]
            else:
                files = [original_file(photo, [preview, self._fetcher.render(device, remote, original, "full")])]
        except RenderFailed as failed:
            return PhotoAnswer(state="unreadable", device=device, ok=False, line=str(failed), photos=[public(photo)])
        when = f", taken {photo.taken_at.replace('T', ' at ')}" if photo.taken_at else ""
        return PhotoAnswer(state="ready", device=device, line=f"{photo.name}{when}.", photos=[public(photo)], files=files)

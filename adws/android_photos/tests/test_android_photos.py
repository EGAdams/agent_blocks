"""Android Photos, offline: a fake phone behind IPhoneReader, real Pillow, a temporary cache. No Windows, no phone.

    cd adws && uv run --quiet --with pydantic --with pytest --with pillow pytest -q android_photos/tests
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from android_photos import adw
from android_photos.capture_time import from_filename, newest_first
from android_photos.interfaces import IPhoneReader
from android_photos.kit import Kit
from android_photos.models import DeviceSeen, Listing, ReaderProblem, RemotePhoto
from android_photos.powershell_reader import PowerShellPhoneReader, wsl_path

PHONE = "Pixel 8"


class FakePhone:
    """IPhoneReader over a dict of name -> (modified, exif date or None). Counts every copy."""

    def __init__(self, files: dict[str, tuple[str, str | None]], name: str = PHONE, storages=("Internal storage",),
                 folder: str = "DCIM/Camera", fail: ReaderProblem | None = None) -> None:
        self.files, self.name, self.storages, self.folder, self.fail = files, name, list(storages), folder, fail
        self.copied: list[str] = []
        self.attached = True

    def look(self) -> list[DeviceSeen]:
        if self.fail:
            raise self.fail
        return [DeviceSeen(name=self.name, storages=self.storages)] if self.attached else []

    def list(self, device: str, folders: list[str]) -> Listing:
        if self.folder not in folders:
            return Listing()
        return Listing(folders=[f"{self.storages[0]}/{self.folder}"],
                       photos=[RemotePhoto(storage=self.storages[0], folder=self.folder, name=n, modified=m)
                               for n, (m, _) in self.files.items()])

    def copy(self, device: str, photos: list[RemotePhoto], into: dict[str, Path]) -> dict[str, Path]:
        out = {}
        for photo in photos:
            if photo.name not in self.files:
                continue
            self.copied.append(photo.name)
            exif = Image.Exif()
            if self.files[photo.name][1]:
                exif[0x0132] = self.files[photo.name][1]
            into[photo.key].mkdir(parents=True, exist_ok=True)
            target = into[photo.key] / photo.name
            Image.new("RGB", (1200, 900), "teal").save(target, exif=exif)
            out[photo.key] = target
        return out


def camera(count: int = 18) -> dict[str, tuple[str, str | None]]:
    return {f"IMG_202611{n:02d}_101500.jpg": (f"2026-11-{n:02d}T10:15:01", None) for n in range(1, count + 1)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ANDROID_PHOTOS_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("ANDROID_PHOTOS_SETTINGS", str(tmp_path / "settings.json"))

    def make(phone: FakePhone, devices=(PHONE,)) -> Kit:
        (tmp_path / "settings.json").write_text(json.dumps({"devices": list(devices)}))
        return Kit(phone)

    def call(kit: Kit, *argv: str) -> tuple[int, dict]:
        out = tmp_path / "answer.json"
        code = adw.run(None, [*argv, "--json", "--json-out", str(out)], kit)
        return code, json.loads(out.read_text())

    make.call = call
    return make


def test_the_reader_interface_has_no_way_to_change_the_phone():
    public = {name for name in vars(IPhoneReader) if not name.startswith("_")}
    assert public == {"look", "list", "copy"}
    script = (Path(adw.__file__).parent / "reader.ps1").read_text()
    for verb in ("Remove-Item", "MoveHere", "Rename-Item", "Move-Item", ".Delete(", "InvokeVerb"):
        assert verb not in script


def test_filename_timestamps_and_ordering():
    assert from_filename("IMG_20261114_093015.jpg") == "2026-11-14T09:30:15"
    assert from_filename("PXL_20261114_143015123.jpg") == "2026-11-14T14:30:15"
    assert from_filename("2026-11-14 09.30.15.jpg") == "2026-11-14T09:30:15"
    assert from_filename("holiday.jpg") == "" and from_filename("IMG_20261399_990000.jpg") == ""
    mixed = [RemotePhoto(storage="s", folder="f", name="holiday.jpg", modified="2026-11-15T00:00:00"),
             RemotePhoto(storage="s", folder="f", name="IMG_20261114_093015.jpg", modified="2020-01-01T00:00:00"),
             RemotePhoto(storage="s", folder="f", name="undated.png")]
    assert [p.name for p in newest_first(mixed)] == ["holiday.jpg", "IMG_20261114_093015.jpg", "undated.png"]


def test_status_names_each_state_and_what_to_do(world):
    assert world.call(world(FakePhone(camera())), "status")[1]["data"]["state"] == "ready"
    gone = FakePhone(camera()); gone.attached = False
    code, answer = world.call(world(gone), "status")
    assert code == 1 and answer["data"]["state"] == "no_device" and "Plug the phone" in answer["line"]
    code, answer = world.call(world(FakePhone(camera(), storages=())), "status")
    assert answer["data"]["state"] == "locked" and "File transfer" in answer["line"]
    code, answer = world.call(world(FakePhone(camera()), devices=()), "status")
    assert answer["data"]["state"] == "not_authorized" and answer["data"]["devices_seen"] == [PHONE]
    assert world.call(world(FakePhone(camera(), folder="Pictures")), "status")[1]["data"]["state"] == "no_camera_folder"
    broken = FakePhone(camera(), fail=ReaderProblem("reader_failed", "COM said no"))
    assert world.call(world(broken), "status")[1]["data"]["state"] == "reader_failed"


def test_an_unauthorized_or_locked_phone_is_never_read(world):
    for phone, devices in ((FakePhone(camera()), ("Someone Else's Phone",)), (FakePhone(camera(), storages=()), (PHONE,))):
        code, answer = world.call(world(phone, devices), "list_recent_photos")
        assert code == 1 and phone.copied == [] and "files" not in answer["data"]


def test_the_default_is_the_fifteen_newest_newest_first_and_only_those_are_copied(world):
    phone = FakePhone(camera(18))
    code, answer = world.call(world(phone), "list_recent_photos")
    photos = answer["data"]["photos"]
    assert code == 0 and len(photos) == 15 and len(phone.copied) == 15
    assert [p["number"] for p in photos] == list(range(1, 16))
    assert photos[0]["name"] == "IMG_20261118_101500.jpg" and photos[-1]["name"] == "IMG_20261104_101500.jpg"
    assert photos[0]["taken_source"] == "filename" and answer["data"]["total_on_phone"] == 18


def test_a_listing_publishes_thumbnails_not_originals(world):
    code, answer = world.call(world(FakePhone(camera(3))), "list_recent_photos", "--count", "3")
    for f in answer["data"]["files"]:
        pages = f["inspection"]["pages"]
        assert f["mime"] == "image/jpeg" and f["path"].endswith(".preview.jpg") and "originals" not in f["path"]
        assert [p["rendition"] for p in pages] == ["preview"] and pages[0]["sha256"] == f["sha256"]
        assert max(pages[0]["width"], pages[0]["height"]) == 512


def test_the_count_is_kept_between_one_and_twenty(world):
    kit = world(FakePhone(camera(25)))
    assert len(world.call(kit, "list_recent_photos", "--count", "500")[1]["data"]["photos"]) == 20
    assert len(world.call(kit, "list_recent_photos", "--count", "0")[1]["data"]["photos"]) == 1


def test_exif_capture_time_wins_over_the_filename(world):
    files = {"IMG_20261110_000000.jpg": ("2026-11-10T00:00:00", "2026:11:20 08:00:00"),
             "IMG_20261112_000000.jpg": ("2026-11-12T00:00:00", None)}
    photos = world.call(world(FakePhone(files)), "list_recent_photos")[1]["data"]["photos"]
    assert [(p["name"], p["taken_source"]) for p in photos] == [("IMG_20261110_000000.jpg", "exif"),
                                                               ("IMG_20261112_000000.jpg", "filename")]
    assert photos[0]["taken_at"] == "2026-11-20T08:00:00"


def test_picture_number_three_is_the_third_one_listed_and_is_not_copied_twice(world):
    phone = FakePhone(camera(6))
    kit = world(phone)
    listed = world.call(kit, "list_recent_photos", "--count", "5")[1]["data"]["photos"]
    code, answer = world.call(kit, "get_photo", "--number", "3")
    assert code == 0 and answer["data"]["photos"][0]["name"] == listed[2]["name"] and len(phone.copied) == 5
    (only,) = answer["data"]["files"]
    assert "originals" in only["path"] and [p["rendition"] for p in only["inspection"]["pages"]] == ["preview", "full"]
    assert "taken 2026-11-04 at 10:15:00" in answer["line"]
    code, answer = world.call(kit, "get_thumbnail", "--name", listed[0]["name"].upper())
    assert code == 0 and answer["data"]["files"][0]["path"].endswith(".preview.jpg")


def test_a_photo_that_was_never_listed_is_not_found(world):
    kit = world(FakePhone(camera(3)))
    code, answer = world.call(kit, "get_photo", "--number", "2")
    assert code == 1 and answer["data"]["state"] == "not_found" and "recent photos first" in answer["line"]
    assert world.call(kit, "get_photo")[0] == 1
    assert world.call(kit, "get_photo", "--name", "IMG_20261102_101500.jpg")[0] == 0
    assert world.call(kit, "get_photo", "--name", "../../secret.jpg")[1]["data"]["state"] == "not_found"


def test_a_disconnect_between_listing_and_opening_is_reported_not_guessed(world):
    phone = FakePhone(camera(3))
    kit = world(phone)
    world.call(kit, "list_recent_photos")
    phone.attached = False
    code, answer = world.call(kit, "get_photo", "--number", "1")
    assert code == 1 and answer["data"]["state"] == "no_device"


def test_a_picture_that_cannot_be_rendered_is_listed_with_a_note(world):
    phone = FakePhone(camera(2))
    real_copy = phone.copy

    def copy(device, photos, into):
        out = real_copy(device, photos, into)
        next(iter(out.values())).write_bytes(b"not an image at all")
        return out

    phone.copy = copy
    data = world.call(world(phone), "list_recent_photos")[1]["data"]
    assert len(data["photos"]) == 2 and len(data["files"]) == 1 and "could not be turned into a picture" in data["notes"][0]


def test_old_cache_entries_are_removed_and_every_request_is_logged(world, tmp_path):
    kit = world(FakePhone(camera(2)))
    world.call(kit, "list_recent_photos", "--client", "chatgpt-bridge", "--task-id", "t1")
    assert "client=chatgpt-bridge task=t1 state=ready photos=IMG_20261102_101500.jpg" in (tmp_path / "cache/requests.log").read_text()
    kit.cache._now = lambda: __import__("time").time() + 25 * 3600
    assert kit.cache.purge() == 4 and not any((tmp_path / "cache/originals").iterdir())
    assert oct((tmp_path / "cache").stat().st_mode)[-3:] == "700"


def test_windows_paths_and_a_machine_without_windows():
    assert wsl_path(r"C:\Users\x\AppData\Local") == Path("/mnt/c/Users/x/AppData/Local")

    class NoLink:
        def ensure(self):
            return False, "no interop socket"

    with pytest.raises(ReaderProblem) as raised:
        PowerShellPhoneReader(NoLink()).look()
    assert raised.value.cause == "no_windows"


def test_the_request_reaches_powershell_as_a_file_never_on_the_command_line(tmp_path):
    seen = []

    class Link:
        def ensure(self):
            return True, ""

    def run(argv, **kw):
        seen.append(argv)
        class Done:
            stdout = "C:\\nowhere\\Local\n"
        return Done()

    reader = PowerShellPhoneReader(Link(), run=run)
    with pytest.raises(ReaderProblem):
        reader.list('x"; Remove-Item C:\\ -Recurse; "', ["DCIM/Camera"])
    assert all("Remove-Item" not in part for argv in seen for part in argv)
